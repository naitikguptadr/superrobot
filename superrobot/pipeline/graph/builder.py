"""Build a whole-repo code graph (modules, functions, classes, and their
import/call/defines relationships) using ast for structure and jedi for
real cross-file call resolution.

Verified behavior (see spec): jedi's Script.infer(line, column) resolves
a call site through to its true cross-file definition. Script.goto()
does NOT do this reliably -- it can stop at the import statement rather
than the original definition. Always use infer(), not goto(), when
resolving call targets for the graph's "calls" edges.
"""

from __future__ import annotations

import ast
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import jedi  # type: ignore[import-untyped]
import networkx as nx  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

_EXCLUDED_DIR_NAMES = {"__pycache__", "venv", ".venv", "node_modules", ".git"}

# Suffix appended by code_object_node_id() to disambiguate a function/class
# node id from a real module's own dotted name, in the rare case the two
# would otherwise collide. Dotted module names are built purely from
# directory/file segments joined by ".", so they can never contain a ":",
# guaranteeing this can never itself collide with anything.
_COLLISION_SUFFIX = ":obj"


def module_dotted_name(py_file: Path, repo_root: Path) -> str:
    """Compute the dotted module name for a .py file relative to repo_root.

    tests/fixtures/pkg/tools.py under repo_root tests/fixtures -> "pkg.tools"
    pkg/__init__.py -> "pkg" (the __init__ segment is dropped)
    """
    rel = py_file.relative_to(repo_root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else py_file.stem


def code_object_node_id(dotted_name: str, graph: nx.DiGraph) -> str:
    """Return the graph node id for a function/class whose fully-qualified
    dotted name is ``dotted_name``.

    Node ids are bare dotted strings shared between two different kinds of
    entities: "a module" (see `module_dotted_name`) and "a function/class
    defined somewhere" (a module's dotted name plus its qualified path
    through any enclosing def/class scopes). These CAN collide: a
    package's ``__init__.py`` defines its top-level functions/classes
    directly under the package's own dotted name (`module_dotted_name`
    drops the ``__init__`` segment), so ``def b(): ...`` inside
    ``pkg/__init__.py`` produces the exact same bare dotted string
    (``"pkg.b"``) as a sibling submodule ``pkg/b.py``'s own module id.
    Without disambiguation, ``graph.add_node()``'s attribute-merge
    behavior means whichever of the two gets processed second silently
    overwrites the other's ``kind``/``path``/``line``, erasing one entity
    from the graph with no error.

    Rather than namespacing every node id in the graph (which would touch
    every consumer -- imports edges, entry-point candidate ids, framework
    detection's prefix matching -- for the sake of a rare case), a
    module's id is left completely alone (the overwhelmingly common path
    is untouched) and only the losing side of an actual collision gets
    disambiguated: if ``dotted_name`` already names a real module node in
    ``graph``, the function/class with that same dotted name resolves to
    a distinct id with a ``:obj`` suffix appended instead (see
    `_COLLISION_SUFFIX` -- a dotted module name can never contain ``:``,
    so the two ids can never collide, in either direction).

    Every place that computes a function/class node id -- the ast-based
    definition pass, jedi call-target resolution, and
    entry_points.py's console-script/main-guard candidate construction --
    must route its dotted name through this function so they all agree on
    the same (possibly disambiguated) id for the same function/class.

    Must only be called once every real module in the repo has already
    been added to ``graph`` (see `build_repo_graph`'s module-registration
    pass) -- otherwise whether a collision is detected would depend on
    file processing order.
    """
    if graph.has_node(dotted_name) and graph.nodes[dotted_name].get("kind") == "module":
        return f"{dotted_name}{_COLLISION_SUFFIX}"
    return dotted_name


def strip_collision_suffix(node_id: str) -> str:
    """Undo `code_object_node_id`'s ``:obj`` disambiguation suffix, if
    present, to recover a node's "natural" dotted name -- e.g. for
    extracting its rightmost/local name segment. See `code_object_node_id`
    for why the suffix exists.
    """
    return node_id.removesuffix(_COLLISION_SUFFIX)


def iter_python_files(repo_root: Path) -> list[Path]:
    """List all .py files under repo_root, skipping venvs/caches.

    Exclusion (hidden dirs, __pycache__/venv/etc.) must be checked against
    the path RELATIVE to repo_root, never the full absolute path -- an
    absolute-path check would also match any dot-prefixed ANCESTOR
    directory above repo_root (e.g. a repo living under ~/.cache/...),
    silently excluding every file in the repo. Mirrors the already-correct
    pattern in superrobot.pipeline.scanner's _collect_python_files, which
    checks `f.relative_to(root).parts`.
    """
    return [
        p
        for p in repo_root.rglob("*.py")
        if not any(
            part in _EXCLUDED_DIR_NAMES or part.startswith(".")
            for part in p.relative_to(repo_root).parts
        )
    ]


@dataclass
class RepoGraph:
    """A whole-repo code graph plus the root it was built from."""

    graph: nx.DiGraph
    repo_root: Path

    def save(self, path: Path) -> None:
        """Serialize the graph to JSON at path."""
        data = nx.node_link_data(self.graph, edges="edges")
        path.write_text(json.dumps(data, default=str))

    @classmethod
    def load(cls, path: Path, repo_root: Path) -> RepoGraph:
        """Load a previously-saved graph from path."""
        data = json.loads(path.read_text())
        graph = nx.node_link_graph(data, edges="edges")
        return cls(graph=graph, repo_root=repo_root)


def _assign_parents(tree: ast.AST) -> None:
    """Set a `.parent` attribute on every child node in the tree.

    ast doesn't track parent pointers itself, so a single pass over the
    tree records, for each node, the node whose child it is. This lets us
    later walk up from any definition to discover its enclosing scopes.
    """
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent  # type: ignore[attr-defined]


def _qualified_name(node: ast.AST) -> list[str]:
    """Collect enclosing FunctionDef/AsyncFunctionDef/ClassDef names for node.

    Walks up the `.parent` chain (set by `_assign_parents`) and gathers the
    names of enclosing function/class scopes, then reverses the result so
    it reads outermost-to-innermost, ending with `node` itself.
    """
    parts: list[str] = []
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parts.append(current.name)
        current = getattr(current, "parent", None)
    parts.reverse()
    return parts


def _walk_own_body(node: ast.AST) -> list[ast.AST]:
    """Like ast.walk(node), but does NOT descend into nested
    FunctionDef/AsyncFunctionDef/ClassDef definitions.

    ast.walk() has no concept of scope boundaries: walking a function's
    body also yields every node inside any function/class nested within
    it. When that's used to find a function's own call sites, a call made
    only inside a nested inner function ends up attributed not just to
    that inner function (correct) but to every enclosing outer function
    too (wrong) -- since those nested definitions are walked separately as
    their own top-level `func_node`/`class` iteration, this helper must
    stop at their boundary so each call site is attributed exactly once,
    to its actual innermost enclosing scope.
    """
    result: list[ast.AST] = [node]
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        result.extend(_walk_own_body(child))
    return result


def _resolve_relative_import(
    mod_name: str, is_package_init: bool, level: int, module: str | None
) -> str | None:
    """Resolve a relative import (``level`` >= 1) to an absolute dotted
    module id, mirroring CPython's real import resolution
    (``importlib._bootstrap._resolve_name``).

    A regular module's ``__package__`` is its dotted name with the last
    segment stripped; a package's ``__init__.py`` is already its own
    ``__package__`` (its dotted name, as returned by
    `module_dotted_name`, needs no further stripping). From that base,
    ``level=1`` uses the base as-is, and each additional level strips one
    more trailing segment.

    For ``from . import x`` / ``from .. import x`` (``module is None``),
    there's no dotted module name to resolve -- only a containing
    package. Since this graph only tracks module-to-module "imports"
    edges (not individual imported symbols), we resolve to that
    containing package itself, which is the simplest correct choice at
    this granularity.

    Returns None if there is no parent package to resolve against (a
    top-level, non-package module) or if ``level`` goes beyond the top of
    the package hierarchy -- matching Python's own "attempted relative
    import beyond top-level package" failure, rather than crashing or
    fabricating a bogus id.
    """
    base = mod_name if is_package_init else mod_name.rsplit(".", 1)[0] if "." in mod_name else ""

    if not base:
        return None

    base_parts = base.split(".")
    strip_count = level - 1
    if strip_count >= len(base_parts):
        return None
    stripped_base = ".".join(base_parts[: len(base_parts) - strip_count])

    return f"{stripped_base}.{module}" if module is not None else stripped_base


def _is_type_checking_guard(node: ast.If) -> bool:
    """True if node's test is `TYPE_CHECKING` or `typing.TYPE_CHECKING`.

    Mirrors the style of entry_points._is_main_guard(): a narrow,
    structural check of the `ast.If` node's test expression, not a full
    symbolic evaluation. `if TYPE_CHECKING:` is the standard mypy/typing
    idiom for imports that only exist for static type-checkers -- they
    are never executed at runtime (`TYPE_CHECKING` is `False` at runtime,
    `True` only to type checkers).
    """
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _type_checking_guarded_node_ids(tree: ast.AST) -> set[int]:
    """Collect id() of every node in the `body` (never the `orelse`) of an
    `if TYPE_CHECKING:` guard anywhere in tree.

    Only the guard's `body` never executes at runtime; an `else` branch on
    such a guard (uncommon, but legal) is real, executed code and must not
    be swept up here.
    """
    guarded_ids: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.If) and _is_type_checking_guard(node)):
            continue
        for stmt in node.body:
            for sub in ast.walk(stmt):
                guarded_ids.add(id(sub))
    return guarded_ids


def build_repo_graph(repo_root: Path) -> RepoGraph:
    """Build a RepoGraph for the Python repo at repo_root.

    Pass 1: ast-based structure (modules, function/class definitions
    with nested-qualified ids, defines/imports edges).
    Pass 2: cross-file call resolution via jedi.Script.infer() -- NOT
    .goto(), which only follows one hop to the import statement rather
    than the true definition (verified against real jedi 0.20 behavior).
    """
    # Normalize to an absolute, symlink-resolved path (same as
    # scanner.scan's `Path(repo_path).resolve()`). Pass 2 keeps a jedi call
    # target only when `target_path.is_relative_to(repo_root)`, and jedi's
    # `Definition.module_path` is always absolute -- so a relative
    # repo_root would make that check False for every single target and
    # silently drop the entire cross-file call graph, with no error.
    repo_root = Path(repo_root).resolve()
    graph = nx.DiGraph()
    py_files = iter_python_files(repo_root)
    file_asts: dict[Path, ast.Module] = {}
    module_names: dict[Path, str] = {}

    # Pass 0: parse every file and register its module node up front,
    # before computing any function/class node id below. code_object_node_id
    # detects a module/function-name collision by checking the graph for an
    # existing "module"-kind node with the same dotted name -- that check
    # must be order-independent (not depend on which file rglob() happens
    # to yield first), so every real module has to already exist in the
    # graph before any function/class id is computed.
    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue

        file_asts[py_file] = tree
        mod_name = module_dotted_name(py_file, repo_root)
        module_names[py_file] = mod_name
        graph.add_node(mod_name, kind="module", path=str(py_file))

    for py_file, tree in file_asts.items():
        mod_name = module_names[py_file]
        _assign_parents(tree)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified_parts = _qualified_name(node)
                dotted_name = f"{mod_name}.{'.'.join(qualified_parts)}"
                qual_id = code_object_node_id(dotted_name, graph)
                node_kind = "class" if isinstance(node, ast.ClassDef) else "function"
                graph.add_node(qual_id, kind=node_kind, path=str(py_file), line=node.lineno)
                graph.add_edge(mod_name, qual_id, kind="defines")

        is_package_init = py_file.stem == "__init__"
        type_checking_guarded_ids = _type_checking_guarded_node_ids(tree)
        # The same module can be imported more than once from the same
        # source file (e.g. once for real, once again inside an
        # `if TYPE_CHECKING:` block). Since `add_edge()` on an
        # already-existing edge OVERWRITES its attributes rather than
        # merging them, calling it once per import statement would make
        # the outcome depend on ast.walk()'s traversal order -- whichever
        # statement is processed last would win, even if an earlier real
        # import should have. Instead, collect every occurrence per
        # (source, target) pair first, and only mark the edge
        # type_checking_only if ALL of its imports were TYPE_CHECKING-only.
        import_targets: dict[str, bool] = {}
        import_target_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                is_guarded = id(node) in type_checking_guarded_ids
                if node.level == 0:
                    import_target_names = [node.module] if node.module else []
                else:
                    resolved_target = _resolve_relative_import(
                        mod_name, is_package_init, node.level, node.module
                    )
                    import_target_names = [resolved_target] if resolved_target is not None else []
            elif isinstance(node, ast.Import):
                is_guarded = id(node) in type_checking_guarded_ids
                import_target_names = [alias.name for alias in node.names]
            else:
                continue

            for import_target_name in import_target_names:
                # A target already known to have a real import stays real
                # regardless of what any other occurrence says.
                import_targets[import_target_name] = (
                    import_targets.get(import_target_name, True) and is_guarded
                )

        for import_target_name, all_guarded in import_targets.items():
            # Only ever set True: a real, executed import is left without
            # the key at all (rather than an explicit False), so
            # downstream consumers can use a simple
            # `.get("type_checking_only", False)` check.
            edge_kwargs = {"type_checking_only": True} if all_guarded else {}
            graph.add_edge(mod_name, import_target_name, kind="imports", **edge_kwargs)

    project = jedi.Project(path=str(repo_root))
    for py_file, tree in file_asts.items():
        mod_name = module_names[py_file]
        try:
            script = jedi.Script(path=str(py_file), project=project)
        except Exception as exc:
            logger.debug("jedi.Script() failed for %s: %s", py_file, exc)
            continue

        for func_node in ast.walk(tree):
            if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            caller_dotted_name = f"{mod_name}.{'.'.join(_qualified_name(func_node))}"
            caller_id = code_object_node_id(caller_dotted_name, graph)

            for call in _walk_own_body(func_node):
                if not isinstance(call, ast.Call):
                    continue
                target = call.func
                line: int | None
                col: int | None
                if isinstance(target, ast.Name):
                    line, col = target.lineno, target.col_offset
                elif isinstance(target, ast.Attribute):
                    line = target.end_lineno
                    col = (
                        target.end_col_offset - len(target.attr)
                        if target.end_col_offset is not None
                        else None
                    )
                else:
                    continue
                if line is None or col is None:
                    continue

                try:
                    inferred = script.infer(line=line, column=col)
                except Exception as exc:
                    logger.debug(
                        "jedi Script.infer() failed for %s at line %s col %s: %s",
                        py_file,
                        line,
                        col,
                        exc,
                    )
                    continue

                for target_def in inferred:
                    if not target_def.module_path or not target_def.full_name:
                        continue
                    target_path = Path(target_def.module_path)
                    if not target_path.is_relative_to(repo_root):
                        continue
                    # jedi's full_name is already fully qualified through
                    # enclosing classes (verified: a method f.search() on
                    # class Foo infers full_name="pkg.tools.Foo.search"),
                    # which matches this graph's node-id scheme exactly.
                    # Do NOT reconstruct from target_def.name alone. Route
                    # it through code_object_node_id too: jedi has no idea
                    # about this graph's own disambiguation scheme, so its
                    # full_name is always the bare (possibly-colliding)
                    # dotted name -- a call can never actually target a
                    # module object, so if that bare name happens to
                    # collide with a real module in the graph, the true
                    # target is the disambiguated function/class id.
                    callee_id = code_object_node_id(target_def.full_name, graph)
                    if callee_id in graph:
                        graph.add_edge(caller_id, callee_id, kind="calls")

    return RepoGraph(graph=graph, repo_root=repo_root)
