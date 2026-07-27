"""Regression gate: the graph-based path must detect the same framework
as today's scanner.py for every existing test fixture, with confidence
equal or higher. Per the spec, any disagreement here is a hard blocker
on cutover -- investigate and fix, do not relax this test.

Also gates that entry-point resolution succeeds at all, which is what
makes the framework comparison above a real test of the graph path
rather than a vacuous one: detect_framework() treats an empty reachable
set as "everything is reachable", so an unresolved entry point makes it
agree with scanner.py trivially, by never consulting the call graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.entry_points import resolve_entry_point
from superrobot.pipeline.graph.framework_detect import detect_framework
from superrobot.pipeline.scanner import scan

FIXTURES_ROOT = Path(__file__).parent.parent.parent.parent / "fixtures"

FIXTURE_DIRS = [
    "langchain_agent",
    "langgraph_research_agent",
    "crewai_agent",
    "llamaindex_agent",
    "autogen_agent",
    "semantic_kernel_agent",
    "haystack_agent",
    "smolagents_agent",
    "raw_async_agent",
]


@pytest.mark.parametrize("fixture_name", FIXTURE_DIRS)
def test_graph_based_detection_matches_or_improves_on_scanner(fixture_name: str) -> None:
    fixture_path = FIXTURES_ROOT / fixture_name
    baseline = scan(fixture_path)

    repo_graph = build_repo_graph(fixture_path)
    entry = resolve_entry_point(repo_graph)
    assert entry is not None, (
        f"{fixture_name}: entry-point resolution returned None, which silently "
        "disables the whole reachability analysis (see the 2026-07-27 cutover plan)"
    )

    result = detect_framework(repo_graph, entry)

    assert result.framework == baseline.detected_framework, (
        f"{fixture_name}: graph-based detected {result.framework!r}, "
        f"scanner.py detected {baseline.detected_framework!r}"
    )
    assert result.confidence >= baseline.confidence, (
        f"{fixture_name}: graph-based confidence {result.confidence} is lower than "
        f"scanner.py's {baseline.confidence}"
    )
