"""Resolve the real entry point of a scanned repo.

Priority order:
1. pyproject.toml [project.scripts] console-script declaration, if present
   and it resolves to a node already in the graph (most authoritative).
2. What is actually invoked from an `if __name__ == "__main__":` guard,
   traced through the graph.
3. Name/filename heuristic scoring over the graph's own function nodes,
   mirroring superrobot.pipeline.scanner's ENTRY_POINT_NAMES /
   ENTRY_PRIORITY ranking (see `_resolve_by_heuristic`). This tier is
   what makes the whole reachability layer apply to real agent repos:
   they are libraries invoked by a framework, not CLIs, so the great
   majority have neither a console script nor a `__main__` guard, and
   without it every such repo resolved to None -- which
   framework_detect.detect_framework() treats as "everything is
   reachable", silently disabling call-graph-based analysis entirely.
4. None -- fully dynamic dispatch (getattr, plugin loaders, etc.) cannot
   be resolved statically by any tool, graph-based or not, and no
   function in the repo is even named like an entry point.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from superrobot.pipeline.graph.builder import (
    RepoGraph,
    code_object_node_id,
    iter_python_files,
    module_dotted_name,
    strip_collision_suffix,
)
from superrobot.pipeline.scanner import ENTRY_POINT_NAMES, ENTRY_PRIORITY

# Filenames scanner._rank_entry_points() rewards with a +20 bonus. Scanner
# compares against the path RELATIVE to the repo root, so the bonus only
# applies to a top-level main.py/app.py/etc, never to a nested
# `pkg/sub/main.py` -- mirrored exactly here so the two rankings can't
# disagree about the same repo.
_ENTRY_FILENAME_BONUS_PATHS = frozenset({"main.py", "app.py", "__main__.py", "agent.py"})
_ENTRY_FILENAME_BONUS = 20
_RUN_PREFIX_BONUS = 10


def resolve_entry_point(repo_graph: RepoGraph) -> str | None:
    """Return the graph node id of the real entry point, or None."""
    console_script_target = _resolve_console_script(repo_graph)
    if console_script_target is not None:
        return console_script_target

    main_guard_target = _resolve_main_guard_call(repo_graph)
    if main_guard_target is not None:
        return main_guard_target

    return _resolve_by_heuristic(repo_graph)


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


def _resolve_by_heuristic(repo_graph: RepoGraph) -> str | None:
    """Rank the graph's own function nodes the way scanner.py ranks the
    entry-point candidates it collects, and return the best one.

    Deliberately mirrors scanner._collect_entry_points' filter
    (ENTRY_POINT_NAMES membership or a "run_" prefix) and
    scanner._rank_entry_points' scoring (ENTRY_PRIORITY + filename bonus +
    "run_" prefix bonus) so the graph-based path and scanner.py can never
    disagree about which function looks most like an entry point.

    The one part of scanner's score not reproduced is its +5 bonus for an
    `async def`: the graph records only kind/path/line per node and tags
    sync and async functions alike as kind="function", so async-ness isn't
    recoverable here. It's a pure tie-breaker in scanner (smaller than
    every other term), so omitting it can only change the outcome between
    two candidates that already tie on name and file -- which the node-id
    tie-break below resolves deterministically anyway.
    """
    candidates: list[tuple[int, str]] = []

    for node_id, attrs in repo_graph.graph.nodes(data=True):
        if attrs.get("kind") != "function":
            continue
        # The node id of a nested/method definition is qualified through
        # its enclosing scopes ("mod.Class.run"), and may carry the
        # module/function collision suffix -- strip that before taking the
        # rightmost segment, or the local name would come out as "run:obj"
        # and match nothing.
        local_name = strip_collision_suffix(node_id).rsplit(".", 1)[-1]
        if local_name not in ENTRY_POINT_NAMES and not local_name.startswith("run_"):
            continue

        score = ENTRY_PRIORITY.get(local_name, 0)
        if _relative_path(repo_graph, attrs.get("path")) in _ENTRY_FILENAME_BONUS_PATHS:
            score += _ENTRY_FILENAME_BONUS
        if local_name.startswith("run_"):
            score += _RUN_PREFIX_BONUS

        candidates.append((score, node_id))

    if not candidates:
        return None
    # Highest score wins; ties break on the node id itself, never on
    # iteration order -- graph node order follows
    # builder.iter_python_files' filesystem enumeration, so without an
    # explicit tie-break the "winner" between two equally-scored
    # candidates could differ between machines, or between runs on the
    # same repo once an unrelated file is added.
    return min(candidates, key=lambda candidate: (-candidate[0], candidate[1]))[1]


def _relative_path(repo_graph: RepoGraph, path: str | None) -> str | None:
    """Path of a node's defining file relative to the repo root, matching
    what scanner.py scores its filename bonus against."""
    if path is None:
        return None
    try:
        return str(Path(path).relative_to(repo_graph.repo_root))
    except ValueError:
        # A graph reloaded from disk against a different repo_root (see
        # RepoGraph.load) can hold paths outside it; just forgo the bonus.
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
