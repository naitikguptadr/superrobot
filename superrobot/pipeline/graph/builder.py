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

import networkx as nx

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
    def load(cls, path: Path, repo_root: Path) -> "RepoGraph":
        """Load a previously-saved graph from path."""
        data = json.loads(path.read_text())
        graph = nx.node_link_graph(data, edges="edges")
        return cls(graph=graph, repo_root=repo_root)


def build_repo_graph(repo_root: Path) -> RepoGraph:
    """Build a RepoGraph for the Python repo at repo_root.

    Two passes: first ast-based structure (modules, function/class
    definitions, defines/imports edges), then a separate call-resolution
    pass (added in a later task) adds "calls" edges via jedi.
    """
    repo_root = Path(repo_root)
    graph = nx.DiGraph()

    for py_file in iter_python_files(repo_root):
        try:
            source = py_file.read_text()
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue

        mod_name = module_dotted_name(py_file, repo_root)
        graph.add_node(mod_name, kind="module", path=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual_id = f"{mod_name}.{node.name}"
                node_kind = "class" if isinstance(node, ast.ClassDef) else "function"
                graph.add_node(qual_id, kind=node_kind, path=str(py_file), line=node.lineno)
                graph.add_edge(mod_name, qual_id, kind="defines")

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                graph.add_edge(mod_name, node.module, kind="imports")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    graph.add_edge(mod_name, alias.name, kind="imports")

    return RepoGraph(graph=graph, repo_root=repo_root)
