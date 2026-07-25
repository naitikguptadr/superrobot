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
# frameworks: FRAMEWORK_IMPORTS' own declaration order, EXCEPT "langchain"
# itself, which is pushed to the very end (lowest priority). "langchain"
# is the generic base layer that more specific frameworks are routinely
# built directly on top of and import too -- a real langgraph app importing
# langchain_core for its `@tool` decorator (see
# tests/fixtures/langgraph_research_agent) is the common case, not a rare
# one. scanner.py already treats "langchain" as the weaker signal of the
# two: it gives it a lower base confidence (0.75 vs. langgraph's 0.9) and
# has an explicit override letting "langgraph" win over "langchain"
# whenever has_state_graph is true. Naively picking whichever framework
# FRAMEWORK_IMPORTS happens to declare first (i.e. "langchain", since its
# prefixes are listed before "langgraph"'s) would make every such repo
# misreport as "langchain" instead of the more specific "langgraph" --
# this ordering keeps the tie-break deterministic while still matching
# scanner.py's real-world behavior.
_FRAMEWORK_PRIORITY: tuple[str, ...] = tuple(
    framework for framework in dict.fromkeys(FRAMEWORK_IMPORTS.values()) if framework != "langchain"
) + ("langchain",)


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

        for func_node in reachable_functions:
            # Find the function's real containing module via the reverse
            # `defines` edge (module --defines--> function), never by
            # string-splitting the node id -- entry points can be nested
            # (e.g. "pkg.sub.run_agent" lives in module "pkg.sub", not "pkg").
            for module_node, _, edge_attrs in graph.in_edges(func_node, data=True):
                if edge_attrs.get("kind") != "defines":
                    continue
                reachable.add(module_node)
                # A framework import only "counts" as reachable if it's
                # actually imported by a module on the entry point's real
                # call path, so pull in that module's `imports` targets too
                # -- except edges tagged type_checking_only=True, which
                # never execute (an `if TYPE_CHECKING:`-guarded import) and
                # so must never make a module look reachable/in-use. Delegate
                # to queries.imports_of() (with its type-checking-only
                # exclusion flag) rather than reimplementing the edge filter
                # inline.
                reachable.update(
                    imports_of(repo_graph, module_node, exclude_type_checking_only=True)
                )

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


def _pick_deterministic_winner(frameworks: set[str]) -> str:
    """Pick a single framework out of a set of simultaneously
    reachable/unreachable candidates, deterministically.

    `frameworks` is built from `graph.nodes(data=True)` iteration, whose
    order is driven by incidental graph-node insertion order (itself driven
    by builder.iter_python_files' filesystem enumeration order) -- not
    something callers should ever depend on. Instead, break the tie using
    the fixed `_FRAMEWORK_PRIORITY` order (derived from FRAMEWORK_IMPORTS'
    own declaration order -- see its comment for why "langchain" is
    special-cased to the end): the first framework name in that order that
    appears in `frameworks` wins, so the same repo always reports the same
    framework no matter which module happens to be processed first.
    """
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
