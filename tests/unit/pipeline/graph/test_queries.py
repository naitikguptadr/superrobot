"""Tests for shared graph query helpers used by scan/transform/validate."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import RepoGraph, build_repo_graph
from superrobot.pipeline.graph.queries import callers_of, imports_of, reachable_from


def _build(tmp_path: Path) -> RepoGraph:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "tools.py").write_text(
        "def search(query: str) -> str:\n    return query\n"
    )
    (tmp_path / "main.py").write_text(
        "from pkg.tools import search\n\n"
        "def run_agent():\n"
        "    return search('hello')\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    return build_repo_graph(tmp_path)


def test_reachable_from_entry_point(tmp_path: Path) -> None:
    repo_graph = _build(tmp_path)
    result = reachable_from(repo_graph, "main.run_agent")
    assert "pkg.tools.search" in result


def test_imports_of_module(tmp_path: Path) -> None:
    repo_graph = _build(tmp_path)
    assert imports_of(repo_graph, "main") == ["pkg.tools"]


def test_callers_of_function(tmp_path: Path) -> None:
    repo_graph = _build(tmp_path)
    assert callers_of(repo_graph, "pkg.tools.search") == ["main.run_agent"]
