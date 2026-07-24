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
    if entry_point is not None:
        # Extract the module from the entry point (e.g., "main.run_agent" -> "main")
        module = entry_point.split(".")[0]
        if module in graph:
            reachable = nx.descendants(graph, module) | {module}

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
