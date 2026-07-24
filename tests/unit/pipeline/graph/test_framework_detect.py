"""Tests for reachability-weighted framework detection."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.entry_points import resolve_entry_point
from superrobot.pipeline.graph.framework_detect import detect_framework


def test_detects_reachable_framework_with_high_confidence(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from langgraph.graph import StateGraph\n\n"
        "def run_agent():\n"
        "    return StateGraph\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework == "langgraph"
    assert result.confidence >= 0.9
    assert result.unreachable_warnings == []


def test_flags_unreachable_framework_import_separately(tmp_path: Path) -> None:
    (tmp_path / "dead_code.py").write_text(
        "from crewai import Agent\n\n"
        "def unused():\n"
        "    return Agent\n"
    )
    (tmp_path / "main.py").write_text(
        "from langgraph.graph import StateGraph\n\n"
        "def run_agent():\n"
        "    return StateGraph\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework == "langgraph"
    assert any("crewai" in warning for warning in result.unreachable_warnings)


def test_returns_unknown_with_low_confidence_when_no_framework_found(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x = 1\n")
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework == "unknown"
    assert result.confidence <= 0.3
