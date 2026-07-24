"""Tests for graph-based entry-point resolution."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.entry_points import resolve_entry_point


def test_resolves_entry_point_from_main_guard(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def run_agent():\n"
        "    return 'ok'\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)

    assert resolve_entry_point(repo_graph) == "main.run_agent"


def test_prefers_pyproject_console_script(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project.scripts]\n"
        'myagent = "main:run_agent"\n'
    )
    (tmp_path / "main.py").write_text(
        "def run_agent():\n"
        "    return 'ok'\n\n"
        "def other():\n"
        "    return 'nope'\n\n"
        "if __name__ == '__main__':\n"
        "    other()\n"
    )
    repo_graph = build_repo_graph(tmp_path)

    assert resolve_entry_point(repo_graph) == "main.run_agent"


def test_returns_none_when_unresolvable(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x = 1\n")
    repo_graph = build_repo_graph(tmp_path)

    assert resolve_entry_point(repo_graph) is None
