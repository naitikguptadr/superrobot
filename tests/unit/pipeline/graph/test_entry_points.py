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
    excludes) fixes this: the guard tier must find nothing at all here.

    What resolve_entry_point() then returns is decided by the tier-3
    name/filename heuristic, which correctly picks the repo's own
    top-level `app.main` -- a real, callable function in non-vendored
    code. Asserting that exact id (rather than the bare `is None` this
    test used before tier 3 existed) keeps the regression this test
    guards fully pinned: the vendored `venv/.../cli.py` placeholder id
    must never be the answer.
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

    assert resolve_entry_point(repo_graph) == "app.main"


def test_falls_back_to_heuristic_when_no_guard_or_console_script(tmp_path: Path) -> None:
    """The common real-world case: an agent library with no __main__ guard
    and no console script, but an obviously-named entry function. Before
    tier 3 existed this returned None, which made the whole reachability
    layer inert (detect_framework treats an empty reachable set as
    "everything is reachable").
    """
    (tmp_path / "main.py").write_text(
        "def helper():\n    return 1\n\ndef run_agent():\n    return helper()\n"
    )
    repo_graph = build_repo_graph(tmp_path)

    assert resolve_entry_point(repo_graph) == "main.run_agent"


def test_heuristic_prefers_higher_priority_name(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def process():\n    return 1\n\ndef run_agent():\n    return 2\n"
    )
    repo_graph = build_repo_graph(tmp_path)

    # scanner.ENTRY_PRIORITY ranks run_agent (100) above process (70).
    assert resolve_entry_point(repo_graph) == "main.run_agent"


def test_heuristic_returns_none_when_nothing_looks_like_an_entry_point(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text("def helper():\n    return 1\n")
    repo_graph = build_repo_graph(tmp_path)

    assert resolve_entry_point(repo_graph) is None
