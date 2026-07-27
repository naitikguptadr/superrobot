"""Framework detection weighted by reachability from the resolved entry
point. Reuses the exact FRAMEWORK_IMPORTS domain-knowledge table from
superrobot.pipeline.scanner -- no static-analysis tool replaces knowing
that a given import name means a given framework; the graph only changes
how confidently we act on that knowledge (is it actually used at runtime,
or just present somewhere in the repo).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx  # type: ignore[import-untyped]

from superrobot.pipeline.graph.builder import RepoGraph
from superrobot.pipeline.graph.queries import imports_of, reachable_from
from superrobot.pipeline.scanner import ENTRY_POINT_NAMES, FRAMEWORK_IMPORTS

# scanner.py's raw-async fallback: no known agent framework import, but the
# repo directly drives an async HTTP client. Kept as a distinct, narrow
# signal (never mistaken for a real framework import) so it only fires once
# every FRAMEWORK_IMPORTS prefix has already come up empty.
RAW_ASYNC_IMPORTS = frozenset({"httpx", "aiohttp"})

# scanner._compute_confidence() adds +0.1 whenever it finds at least one
# function whose name looks like a real entry point (see
# scanner.ENTRY_POINT_NAMES / the "run_" prefix convention). The graph-based
# path mirrors that exact bonus so confidence here is never lower than
# scanner.py's for the same repo, even when resolve_entry_point() can't
# find a `if __name__ == "__main__":` guard or console-script (most real
# fixtures have neither) and reachability falls back to "everything counts".
_ENTRY_SIGNAL_BONUS = 0.1

# Deterministic tie-break order for simultaneously reachable/unreachable
# frameworks: plain FRAMEWORK_IMPORTS declaration order, with NO blanket
# demotion of "langchain". A repo that has both, say, "langchain_core" and
# "crewai" reachable with no langgraph involved is genuinely ambiguous, and
# there's no general justification for always preferring crewai (or any
# other framework) over langchain in that case -- so the plain declaration
# order applies, same as for every other pair.
_FRAMEWORK_PRIORITY: tuple[str, ...] = tuple(dict.fromkeys(FRAMEWORK_IMPORTS.values()))

# The ONE real, narrow exception: langgraph vs. langchain specifically.
# scanner.py's actual override (see `has_state_graph` in scanner.py) only
# ever promotes "langchain" to "langgraph", and only when a `StateGraph`
# symbol is genuinely used -- it never lets langgraph/langchain detection
# influence any other framework. That's because langchain_core is the
# generic base layer langgraph is built on top of (a real langgraph app
# routinely imports langchain_core too, e.g. for its `@tool` decorator --
# see tests/fixtures/langgraph_research_agent), so "both reachable" is a
# strong signal the repo is really a langgraph app, not a langchain app
# that happens to also touch langgraph. The graph-based path doesn't track
# StateGraph symbol usage (unlike scanner.py's AST walk), so it uses "both
# imports simultaneously reachable" as its proxy for that same signal --
# but, crucially, this override is scoped to ONLY this pair, not a blanket
# "langchain always loses" rule that would misfire on every other
# langchain-vs-something-else tie (e.g. langchain-vs-crewai, where there's
# no such relationship and the plain declaration order should apply).
_LANGGRAPH_BEATS_LANGCHAIN = ("langgraph", "langchain")


@dataclass
class FrameworkDetection:
    framework: str
    confidence: float
    unreachable_warnings: list[str] = field(default_factory=list)


def detect_framework(repo_graph: RepoGraph, entry_point: str | None) -> FrameworkDetection:
    """Detect the primary framework, weighting reachability from entry_point."""
    graph = repo_graph.graph

    reachable: set[str] = set()
    if entry_point is not None and entry_point in graph:
        # Functions/classes actually reachable from the entry point via real
        # `calls` edges (already correct: this is a genuine call-graph walk).
        # Delegate to queries.reachable_from() rather than reimplementing
        # nx.descendants() inline -- centralizing this logic in queries.py
        # is the entire point of that module.
        reachable_functions = reachable_from(repo_graph, entry_point) | {entry_point}
        reachable |= reachable_functions

        defining_modules: set[str] = set()
        for func_node in reachable_functions:
            # Find the function's real containing module via the reverse
            # `defines` edge (module --defines--> function), never by
            # string-splitting the node id -- entry points can be nested
            # (e.g. "pkg.sub.run_agent" lives in module "pkg.sub", not "pkg").
            for module_node, _, edge_attrs in graph.in_edges(func_node, data=True):
                if edge_attrs.get("kind") == "defines":
                    defining_modules.add(module_node)

        reachable |= _transitive_imports(repo_graph, defining_modules)

    reachable_frameworks: set[str] = set()
    unreachable_frameworks: set[str] = set()
    unreachable_warnings: list[str] = []

    for node, attrs in graph.nodes(data=True):
        # Check both local modules and external imports (which may have no 'kind')
        kind = attrs.get("kind")
        if kind not in ("module", None):
            continue
        if _is_type_checking_only_import(graph, node):
            # This module's only presence in the graph is an
            # `if TYPE_CHECKING:`-guarded import, which never executes at
            # runtime. That's a different situation from a genuinely
            # unreachable (but real, executed) import: there's no code to
            # warn about as a possible abandoned migration, since the
            # import was never meant to run in the first place. Skip it
            # from detection entirely rather than counting it as reachable
            # OR folding it into unreachable_warnings with language that
            # would misrepresent it as dead runtime code.
            continue
        for prefix, framework in FRAMEWORK_IMPORTS.items():
            if node != prefix and not node.startswith(prefix + "."):
                continue
            is_reachable = (not reachable) or (node in reachable)
            if is_reachable:
                reachable_frameworks.add(framework)
            else:
                unreachable_frameworks.add(framework)
                unreachable_warnings.append(
                    f"unreachable framework import found: {framework} ({node}) -- "
                    "confirm this isn't leftover from an abandoned migration"
                )

    entry_bonus = _ENTRY_SIGNAL_BONUS if _has_entry_point_signal(graph, reachable) else 0.0

    if reachable_frameworks:
        framework = _pick_deterministic_winner(reachable_frameworks)
        return FrameworkDetection(
            framework=framework,
            confidence=min(1.0, 0.9 + entry_bonus),
            unreachable_warnings=unreachable_warnings,
        )

    if unreachable_frameworks:
        framework = _pick_deterministic_winner(unreachable_frameworks)
        return FrameworkDetection(
            framework=framework,
            confidence=min(1.0, 0.4 + entry_bonus),
            unreachable_warnings=unreachable_warnings,
        )

    if _has_raw_async_imports(graph, reachable):
        return FrameworkDetection(
            framework="raw_async",
            confidence=min(1.0, 0.4 + entry_bonus),
            unreachable_warnings=unreachable_warnings,
        )

    return FrameworkDetection(framework="unknown", confidence=min(1.0, 0.2 + entry_bonus))


def _transitive_imports(repo_graph: RepoGraph, seed_modules: set[str]) -> set[str]:
    """Return `seed_modules` plus everything reachable from them by following
    `imports` edges to a fixpoint.

    A framework import only "counts" as reachable if it's actually imported
    -- directly or indirectly -- by a module on the entry point's real call
    path. Import reachability is inherently transitive: if module A executes
    `import B` and B executes `import langchain_core.tools`, then importing
    A really does execute that framework import, so following only ONE hop
    (A's direct imports) wrongly reports the framework as dead code.

    Edges tagged type_checking_only=True are excluded at EVERY hop, not just
    the first: an `if TYPE_CHECKING:`-guarded import never executes, so it
    can't make its target reachable -- and therefore can't make anything
    downstream of that target reachable either. queries.imports_of() applies
    that filter for us, so the walk delegates to it per hop rather than
    reimplementing the edge check.

    This is a visited-set BFS rather than a recursive descent specifically so
    circular imports (legal, and common in real repos) terminate instead of
    recursing forever: a module already in `visited` is never expanded twice.
    """
    visited = set(seed_modules)
    queue = list(seed_modules)
    while queue:
        module_node = queue.pop()
        for imported in imports_of(repo_graph, module_node, exclude_type_checking_only=True):
            if imported not in visited:
                visited.add(imported)
                queue.append(imported)
    return visited


def _pick_deterministic_winner(frameworks: set[str]) -> str:
    """Pick a single framework out of a set of simultaneously
    reachable/unreachable candidates, deterministically.

    `frameworks` is built from `graph.nodes(data=True)` iteration, whose
    order is driven by incidental graph-node insertion order (itself driven
    by builder.iter_python_files' filesystem enumeration order) -- not
    something callers should ever depend on. Instead, break the tie using
    the fixed `_FRAMEWORK_PRIORITY` order (FRAMEWORK_IMPORTS' own
    declaration order), with the one narrow exception of
    `_LANGGRAPH_BEATS_LANGCHAIN` (see its comment) checked first: the first
    framework name that applies wins, so the same repo always reports the
    same framework no matter which module happens to be processed first.
    """
    if all(framework in frameworks for framework in _LANGGRAPH_BEATS_LANGCHAIN):
        return "langgraph"
    for framework in _FRAMEWORK_PRIORITY:
        if framework in frameworks:
            return framework
    # Unreachable in practice: `frameworks` is only ever populated with
    # values drawn directly from FRAMEWORK_IMPORTS itself.
    return next(iter(frameworks))


def _is_type_checking_only_import(graph: nx.DiGraph, node: str) -> bool:
    """True if every "imports" edge targeting node is tagged
    type_checking_only=True -- i.e. this module's only presence in the
    graph comes from an `if TYPE_CHECKING:`-guarded import, which never
    executes at runtime. A node with no "imports" in-edges at all (e.g. a
    real module defined in the repo with no importer) is NOT considered
    type-checking-only here; this only applies when there's at least one
    "imports" edge and none of them are real.
    """
    import_edge_attrs = [
        attrs for _, _, attrs in graph.in_edges(node, data=True) if attrs.get("kind") == "imports"
    ]
    return bool(import_edge_attrs) and all(
        attrs.get("type_checking_only", False) for attrs in import_edge_attrs
    )


def _has_entry_point_signal(graph: nx.DiGraph, reachable: set[str]) -> bool:
    """True if a function shaped like a real entry point exists in scope.

    Mirrors scanner.py's ENTRY_POINT_NAMES / "run_"-prefix convention (see
    scanner._collect_entry_points / _rank_entry_points), which is what
    scanner._compute_confidence() rewards with its own +0.1 bonus. Scoped to
    `reachable` when reachability narrowing actually happened, otherwise
    (no resolvable entry point) falls back to scanning every function node,
    matching how the reachable-framework check above already treats an
    empty `reachable` set as "everything counts".
    """
    for node, attrs in graph.nodes(data=True):
        if attrs.get("kind") != "function":
            continue
        if reachable and node not in reachable:
            continue
        name = node.rsplit(".", 1)[-1]
        if name in ENTRY_POINT_NAMES or name.startswith("run_"):
            return True
    return False


def _has_raw_async_imports(graph: nx.DiGraph, reachable: set[str]) -> bool:
    """True if the repo imports a raw async HTTP client (httpx/aiohttp).

    This is the graph-based equivalent of scanner._has_raw_async(): once
    every FRAMEWORK_IMPORTS prefix has come up empty, an import of a raw
    async HTTP client is the same signal scanner.py uses to call a repo
    "raw_async" instead of "unknown".
    """
    for node, attrs in graph.nodes(data=True):
        kind = attrs.get("kind")
        if kind not in ("module", None):
            continue
        if node not in RAW_ASYNC_IMPORTS and not any(
            node.startswith(prefix + ".") for prefix in RAW_ASYNC_IMPORTS
        ):
            continue
        if (not reachable) or (node in reachable):
            return True
    return False
