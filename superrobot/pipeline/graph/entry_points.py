"""Resolve the real entry point of a scanned repo.

Priority order:
1. pyproject.toml [project.scripts] console-script declaration, if present
   and it resolves to a node already in the graph (most authoritative).
2. What is actually invoked from an `if __name__ == "__main__":` guard,
   traced through the graph.
3. None -- callers should fall back to the existing name/filename
   heuristic scoring in superrobot.pipeline.scanner when this returns
   None. Fully dynamic dispatch (getattr, plugin loaders, etc.) cannot
   be resolved statically by any tool, graph-based or not.
"""

from __future__ import annotations

import ast
import tomllib

from superrobot.pipeline.graph.builder import (
    RepoGraph,
    code_object_node_id,
    iter_python_files,
    module_dotted_name,
)


def resolve_entry_point(repo_graph: RepoGraph) -> str | None:
    """Return the graph node id of the real entry point, or None."""
    console_script_target = _resolve_console_script(repo_graph)
    if console_script_target is not None:
        return console_script_target

    return _resolve_main_guard_call(repo_graph)


def _resolve_console_script(repo_graph: RepoGraph) -> str | None:
    pyproject_path = repo_graph.repo_root / "pyproject.toml"
    if not pyproject_path.is_file():
        return None
    try:
        data = tomllib.loads(pyproject_path.read_text())
    except tomllib.TOMLDecodeError:
        return None

    scripts = data.get("project", {}).get("scripts", {})
    for target in scripts.values():
        module_part, _, func_part = target.partition(":")
        if not func_part:
            continue
        # Route through code_object_node_id: the bare "module.func" dotted
        # name this constructs could collide with a real module's own
        # dotted name (e.g. a console-script target `pkg:b` where pkg's
        # __init__.py defines `b` AND a sibling module pkg/b.py exists),
        # in which case the function actually lives at the disambiguated
        # id -- see builder.code_object_node_id.
        candidate = code_object_node_id(f"{module_part}.{func_part}", repo_graph.graph)
        if candidate in repo_graph.graph:
            return candidate
    return None


def _resolve_main_guard_call(repo_graph: RepoGraph) -> str | None:
    # Use the same exclusion logic that already built repo_graph itself
    # (venvs/caches/vendored dirs/hidden dirs) -- a raw, unfiltered
    # rglob("*.py") would walk into vendored dependency code that was
    # never part of the graph in the first place, letting some installed
    # package's own CLI script's `if __name__ == "__main__":` guard be
    # mistaken for the repo's real entry point.
    for py_file in iter_python_files(repo_graph.repo_root):
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue

        mod_name = module_dotted_name(py_file, repo_graph.repo_root)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.If) and _is_main_guard(node)):
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                    candidate = f"{mod_name}.{call.func.id}"
                    if candidate in repo_graph.graph:
                        return candidate
    return None


def _is_main_guard(node: ast.If) -> bool:
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(
            isinstance(comparator, ast.Constant) and comparator.value == "__main__"
            for comparator in test.comparators
        )
    )
