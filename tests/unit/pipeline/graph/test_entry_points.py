"""Tests for graph-based entry-point resolution."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.entry_points import resolve_entry_point


def test_resolves_entry_point_from_main_guard(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def run_agent():\n    return 'ok'\n\nif __name__ == '__main__':\n    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)

    assert resolve_entry_point(repo_graph) == "main.run_agent"


def test_prefers_pyproject_console_script(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\nmyagent = "main:run_agent"\n')
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


def test_resolve_entry_point_skips_broken_symlink_without_raising(tmp_path: Path) -> None:
    """A broken/dangling symlink anywhere under repo_root must not crash
    _resolve_main_guard_call's own file-reading loop (a duplicate of the
    same read/parse pattern in builder.py) -- FileNotFoundError is an
    OSError subclass and rglob("*.py") matches by name alone, without
    checking the symlink resolves."""
    import os

    (tmp_path / "main.py").write_text(
        "def run_agent():\n    return 'ok'\n\nif __name__ == '__main__':\n    run_agent()\n"
    )
    os.symlink(tmp_path / "does_not_exist.py", tmp_path / "broken_link.py")
    repo_graph = build_repo_graph(tmp_path)

    assert resolve_entry_point(repo_graph) == "main.run_agent"
