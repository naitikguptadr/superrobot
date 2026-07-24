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

from superrobot.pipeline.graph.builder import RepoGraph, module_dotted_name


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
        candidate = f"{module_part}.{func_part}"
        if candidate in repo_graph.graph:
            return candidate
    return None


def _resolve_main_guard_call(repo_graph: RepoGraph) -> str | None:
    for py_file in repo_graph.repo_root.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
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
