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


def test_detects_reachable_framework_with_nested_entry_point(tmp_path: Path) -> None:
    """Regression test: the entry point's containing module must be found via
    real 'defines' graph edges, not by naively splitting the entry point id on
    its first '.'. A nested entry point like "pkg.sub.run_agent" lives in
    module "pkg.sub" -- string-splitting on the first dot would incorrectly
    look for a module node named "pkg", which doesn't exist in the graph.
    """
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "sub.py").write_text(
        "from langgraph.graph import StateGraph\n\n"
        "def run_agent():\n"
        "    return StateGraph\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    assert entry == "pkg.sub.run_agent"

    result = detect_framework(repo_graph, entry)

    assert result.framework == "langgraph"
    assert result.confidence >= 0.9
    assert result.unreachable_warnings == []


def test_returns_unknown_with_low_confidence_when_no_framework_found(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x = 1\n")
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework == "unknown"
    assert result.confidence <= 0.3
