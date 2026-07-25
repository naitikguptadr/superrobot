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


def test_ignores_main_guard_inside_vendored_venv_directory(tmp_path: Path) -> None:
    """_resolve_main_guard_call() must not walk into .venv/venv/node_modules/
    etc, unlike builder.iter_python_files() (used to build the graph
    itself), which already excludes them.

    Reproduction: the real, non-vendored code imports a deep dotted name
    (`venv.lib.some_package.cli.main`) -- this is enough on its own to add
    a bare placeholder node with that exact id to the graph (an import
    target, not a real callable; see builder.py's "imports" edge
    handling), regardless of whether any such package is actually
    installed. Separately, a *vendored* file living under a real
    `venv/` directory (matching builder._EXCLUDED_DIR_NAMES) has its own
    `if __name__ == "__main__": main()` guard. Because
    `_resolve_main_guard_call` used to walk every .py file with a raw,
    unfiltered `rglob("*.py")`, it would find that vendored guard, compute
    its candidate id as "venv.lib.some_package.cli.main" (from the
    vendored file's own path), see that this string happens to already be
    a node in the graph (purely because of the unrelated import above),
    and incorrectly return it as "the" entry point -- even though it does
    not correspond to any real, callable function at all, and the actual
    repo code (app.py) has no `__main__` guard of its own. Excluding
    vendored directories (matching what the graph itself already
    excludes) fixes this: resolve_entry_point() must fall through to None.
    """
    (tmp_path / "app.py").write_text(
        "import venv.lib.some_package.cli.main as _unused\n\ndef main():\n    return 'real'\n"
    )
    venv_pkg_dir = tmp_path / "venv" / "lib" / "some_package"
    venv_pkg_dir.mkdir(parents=True)
    (venv_pkg_dir / "__init__.py").write_text("")
    (venv_pkg_dir / "cli.py").write_text('if __name__ == "__main__":\n    main()\n')

    repo_graph = build_repo_graph(tmp_path)
    assert "venv.lib.some_package.cli.main" in repo_graph.graph

    assert resolve_entry_point(repo_graph) is None
