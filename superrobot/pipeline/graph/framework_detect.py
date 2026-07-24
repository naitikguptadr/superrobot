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
        reachable_functions = nx.descendants(graph, entry_point) | {entry_point}
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
                # call path, so pull in that module's `imports` targets too.
                for _, imported, imports_attrs in graph.out_edges(module_node, data=True):
                    if imports_attrs.get("kind") == "imports":
                        reachable.add(imported)

    reachable_frameworks: dict[str, str] = {}
    unreachable_frameworks: dict[str, str] = {}
    unreachable_warnings: list[str] = []

    for node, attrs in graph.nodes(data=True):
        # Check both local modules and external imports (which may have no 'kind')
        kind = attrs.get("kind")
        if kind not in ("module", None):
            continue
        for prefix, framework in FRAMEWORK_IMPORTS.items():
            if node != prefix and not node.startswith(prefix + "."):
                continue
            is_reachable = (not reachable) or (node in reachable)
            if is_reachable:
                reachable_frameworks.setdefault(framework, node)
            else:
                unreachable_frameworks.setdefault(framework, node)
                unreachable_warnings.append(
                    f"unreachable framework import found: {framework} ({node}) -- "
                    "confirm this isn't leftover from an abandoned migration"
                )

    entry_bonus = _ENTRY_SIGNAL_BONUS if _has_entry_point_signal(graph, reachable) else 0.0

    if reachable_frameworks:
        framework = next(iter(reachable_frameworks))
        return FrameworkDetection(
            framework=framework,
            confidence=min(1.0, 0.9 + entry_bonus),
            unreachable_warnings=unreachable_warnings,
        )

    if unreachable_frameworks:
        framework = next(iter(unreachable_frameworks))
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

    return FrameworkDetection(framework="unknown", confidence=0.2)


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
