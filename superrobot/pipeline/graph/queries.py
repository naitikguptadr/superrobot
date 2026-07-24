"""Shared, reusable graph queries used by scan, transform, and validate
so all three stages reason about the same repo structure instead of
each doing an independent ad hoc pass.
"""

from __future__ import annotations

import networkx as nx  # type: ignore[import-untyped]

from superrobot.pipeline.graph.builder import RepoGraph


def reachable_from(repo_graph: RepoGraph, node_id: str) -> set[str]:
    """Return all nodes reachable from node_id (excluding node_id itself)."""
    if node_id not in repo_graph.graph:
        return set()
    return set(nx.descendants(repo_graph.graph, node_id))


def imports_of(repo_graph: RepoGraph, module_node_id: str) -> list[str]:
    """Return the module names directly imported by module_node_id."""
    graph = repo_graph.graph
    if module_node_id not in graph:
        return []
    return [
        target
        for target in graph.successors(module_node_id)
        if graph.get_edge_data(module_node_id, target).get("kind") == "imports"
    ]


def callers_of(repo_graph: RepoGraph, function_node_id: str) -> list[str]:
    """Return the function/class nodes that call function_node_id."""
    graph = repo_graph.graph
    if function_node_id not in graph:
        return []
    return [
        source
        for source in graph.predecessors(function_node_id)
        if graph.get_edge_data(source, function_node_id).get("kind") == "calls"
    ]
