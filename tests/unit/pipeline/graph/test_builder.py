"""Tests for RepoGraph construction, save, and load."""

from __future__ import annotations

import json
from pathlib import Path

from superrobot.pipeline.graph.builder import RepoGraph, module_dotted_name


def test_module_dotted_name_for_top_level_file(tmp_path: Path) -> None:
    repo_root = tmp_path
    py_file = repo_root / "main.py"
    py_file.write_text("")
    assert module_dotted_name(py_file, repo_root) == "main"


def test_module_dotted_name_for_nested_file(tmp_path: Path) -> None:
    repo_root = tmp_path
    py_file = repo_root / "pkg" / "tools.py"
    py_file.parent.mkdir()
    py_file.write_text("")
    assert module_dotted_name(py_file, repo_root) == "pkg.tools"


def test_module_dotted_name_strips_init(tmp_path: Path) -> None:
    repo_root = tmp_path
    py_file = repo_root / "pkg" / "__init__.py"
    py_file.parent.mkdir()
    py_file.write_text("")
    assert module_dotted_name(py_file, repo_root) == "pkg"


def test_save_and_load_round_trips_graph(tmp_path: Path) -> None:
    import networkx as nx

    graph = nx.DiGraph()
    graph.add_node("main", kind="module", path="/tmp/main.py")
    graph.add_node("main.run", kind="function", path="/tmp/main.py", line=3)
    graph.add_edge("main", "main.run", kind="defines")

    repo_graph = RepoGraph(graph=graph, repo_root=tmp_path)
    out_path = tmp_path / "graph.json"
    repo_graph.save(out_path)

    assert out_path.is_file()
    data = json.loads(out_path.read_text())
    assert data["directed"] is True

    loaded = RepoGraph.load(out_path, repo_root=tmp_path)
    assert set(loaded.graph.nodes) == {"main", "main.run"}
    assert loaded.graph.nodes["main.run"]["kind"] == "function"
    assert loaded.graph.has_edge("main", "main.run")


def test_build_repo_graph_structure_pass(tmp_path: Path) -> None:
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "tools.py").write_text(
        "def search(query: str) -> str:\n    return query\n"
    )
    (tmp_path / "main.py").write_text(
        "from pkg.tools import search\n\n"
        "def run_agent():\n"
        "    return search('hello')\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    assert graph.nodes["main"]["kind"] == "module"
    assert graph.nodes["pkg.tools"]["kind"] == "module"
    assert graph.nodes["main.run_agent"]["kind"] == "function"
    assert graph.nodes["main.run_agent"]["line"] == 3
    assert graph.nodes["pkg.tools.search"]["kind"] == "function"

    assert graph.has_edge("main", "main.run_agent")
    assert graph.get_edge_data("main", "main.run_agent")["kind"] == "defines"
    assert graph.has_edge("pkg.tools", "pkg.tools.search")

    assert graph.has_edge("main", "pkg.tools")
    assert graph.get_edge_data("main", "pkg.tools")["kind"] == "imports"
