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
