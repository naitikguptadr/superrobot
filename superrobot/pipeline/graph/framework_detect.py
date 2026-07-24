"""Framework detection weighted by reachability from the resolved entry
point. Reuses the exact FRAMEWORK_IMPORTS domain-knowledge table from
superrobot.pipeline.scanner -- no static-analysis tool replaces knowing
that a given import name means a given framework; the graph only changes
how confidently we act on that knowledge (is it actually used at runtime,
or just present somewhere in the repo).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from superrobot.pipeline.graph.builder import RepoGraph
from superrobot.pipeline.scanner import FRAMEWORK_IMPORTS


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

    if reachable_frameworks:
        framework = next(iter(reachable_frameworks))
        return FrameworkDetection(
            framework=framework, confidence=0.95, unreachable_warnings=unreachable_warnings
        )

    if unreachable_frameworks:
        framework = next(iter(unreachable_frameworks))
        return FrameworkDetection(
            framework=framework, confidence=0.4, unreachable_warnings=unreachable_warnings
        )

    return FrameworkDetection(framework="unknown", confidence=0.2)
