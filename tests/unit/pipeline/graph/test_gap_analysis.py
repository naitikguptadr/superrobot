"""Tests for the graph-native gap analysis check: flagging unreachable
framework imports as a distinct, non-blocking finding. This check was
not previously possible with gap_analysis.py's file-scan approach,
since it requires knowing what's reachable from the entry point.
"""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.entry_points import resolve_entry_point
from superrobot.pipeline.graph.gap_analysis import check_unreachable_frameworks


def test_flags_unreachable_framework_as_warning(tmp_path: Path) -> None:
    (tmp_path / "dead_code.py").write_text(
        "from crewai import Agent\n\ndef unused():\n    return Agent\n"
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

    findings = check_unreachable_frameworks(repo_graph, entry)

    assert len(findings) == 1
    assert findings[0].rule == "unreachable-framework-import"
    assert findings[0].severity == "warning"
    assert "crewai" in findings[0].message


def test_no_findings_when_everything_reachable(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from langgraph.graph import StateGraph\n\n"
        "def run_agent():\n"
        "    return StateGraph\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    findings = check_unreachable_frameworks(repo_graph, entry)

    assert findings == []


def test_no_findings_when_entry_point_unresolved(tmp_path: Path) -> None:
    """Locks in documented (not accidental) behavior: when entry_point is
    None -- the common case per entry_points.py's own docstring, since most
    real repos have neither a `__main__` guard nor a console script --
    detect_framework() treats an empty `reachable` set as "everything
    counts as reachable", so nothing is ever flagged unreachable and
    check_unreachable_frameworks() always returns []. Even with a dead
    crewai import present and unresolvable, no finding should be produced.
    """
    (tmp_path / "dead_code.py").write_text(
        "from crewai import Agent\n\ndef unused():\n    return Agent\n"
    )
    (tmp_path / "main.py").write_text("x = 1\n")
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)
    assert entry is None

    findings = check_unreachable_frameworks(repo_graph, entry)

    assert findings == []
