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
from dataclasses import dataclass
from pathlib import Path

import jedi  # type: ignore[import-untyped]
import networkx as nx  # type: ignore[import-untyped]

_EXCLUDED_DIR_NAMES = {"__pycache__", "venv", ".venv", "node_modules", ".git"}


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


def iter_python_files(repo_root: Path) -> list[Path]:
    """List all .py files under repo_root, skipping venvs/caches."""
    return [
        p
        for p in repo_root.rglob("*.py")
        if not any(part in _EXCLUDED_DIR_NAMES or part.startswith(".") for part in p.parts)
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


def build_repo_graph(repo_root: Path) -> RepoGraph:
    """Build a RepoGraph for the Python repo at repo_root.

    Pass 1: ast-based structure (modules, function/class definitions
    with nested-qualified ids, defines/imports edges).
    Pass 2: cross-file call resolution via jedi.Script.infer() -- NOT
    .goto(), which only follows one hop to the import statement rather
    than the true definition (verified against real jedi 0.20 behavior).
    """
    repo_root = Path(repo_root)
    graph = nx.DiGraph()
    py_files = iter_python_files(repo_root)
    file_asts: dict[Path, ast.Module] = {}
    module_names: dict[Path, str] = {}

    for py_file in py_files:
        try:
            source = py_file.read_text()
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue

        file_asts[py_file] = tree
        mod_name = module_dotted_name(py_file, repo_root)
        module_names[py_file] = mod_name
        graph.add_node(mod_name, kind="module", path=str(py_file))

        _assign_parents(tree)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified_parts = _qualified_name(node)
                qual_id = f"{mod_name}.{'.'.join(qualified_parts)}"
                node_kind = "class" if isinstance(node, ast.ClassDef) else "function"
                graph.add_node(qual_id, kind=node_kind, path=str(py_file), line=node.lineno)
                graph.add_edge(mod_name, qual_id, kind="defines")

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                graph.add_edge(mod_name, node.module, kind="imports")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    graph.add_edge(mod_name, alias.name, kind="imports")

    project = jedi.Project(path=str(repo_root))
    for py_file, tree in file_asts.items():
        mod_name = module_names[py_file]
        try:
            script = jedi.Script(path=str(py_file), project=project)
        except Exception:
            continue

        for func_node in ast.walk(tree):
            if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            caller_id = f"{mod_name}.{'.'.join(_qualified_name(func_node))}"

            for call in ast.walk(func_node):
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
                except Exception:
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
                    # Do NOT reconstruct from target_def.name alone.
                    callee_id = target_def.full_name
                    if callee_id in graph:
                        graph.add_edge(caller_id, callee_id, kind="calls")

    return RepoGraph(graph=graph, repo_root=repo_root)
