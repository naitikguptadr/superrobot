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


def test_build_repo_graph_qualifies_nested_method_names(tmp_path: Path) -> None:
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "tools.py").write_text(
        "def search(query: str) -> str:\n"
        "    return query\n\n"
        "class Foo:\n"
        "    def search(self, query: str) -> str:\n"
        "        return query\n"
    )

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    # The top-level function must still exist under its own id.
    assert graph.nodes["pkg.tools.search"]["kind"] == "function"

    # The method must be qualified through its enclosing class, not collide
    # with the top-level function of the same name.
    assert graph.nodes["pkg.tools.Foo.search"]["kind"] == "function"
    assert graph.nodes["pkg.tools.Foo"]["kind"] == "class"

    assert graph.has_edge("pkg.tools", "pkg.tools.search")
    assert graph.has_edge("pkg.tools", "pkg.tools.Foo")


def test_build_repo_graph_resolves_cross_file_calls(tmp_path: Path) -> None:
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

    assert graph.has_edge("main.run_agent", "pkg.tools.search")
    assert graph.get_edge_data("main.run_agent", "pkg.tools.search")["kind"] == "calls"


def test_build_repo_graph_resolves_relative_import_from_sibling(tmp_path: Path) -> None:
    """`from .sibling import x` inside pkg/sub/mod.py must resolve to the
    real sibling module id "pkg.sub.sibling", not the bare "sibling"."""
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "sub").mkdir()
    (tmp_path / "pkg" / "sub" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "sub" / "sibling.py").write_text("x = 1\n")
    (tmp_path / "pkg" / "sub" / "mod.py").write_text("from .sibling import x\n")

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    assert graph.has_edge("pkg.sub.mod", "pkg.sub.sibling")
    assert graph.get_edge_data("pkg.sub.mod", "pkg.sub.sibling")["kind"] == "imports"
    # The unresolved bare name must NOT show up as the edge target.
    assert not graph.has_edge("pkg.sub.mod", "sibling")


def test_build_repo_graph_resolves_bare_relative_import_to_containing_package(
    tmp_path: Path,
) -> None:
    """`from . import x` (level=1, module=None) has no dotted module name to
    resolve, only a containing package -- we record an "imports" edge to
    that containing package itself (pkg.sub), which is the right
    granularity since this graph only tracks module-to-module edges, not
    individual imported symbols."""
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "sub").mkdir()
    (tmp_path / "pkg" / "sub" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "sub" / "sibling_module.py").write_text("")
    (tmp_path / "pkg" / "sub" / "mod.py").write_text("from . import sibling_module\n")

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    assert graph.has_edge("pkg.sub.mod", "pkg.sub")
    assert graph.get_edge_data("pkg.sub.mod", "pkg.sub")["kind"] == "imports"


def test_build_repo_graph_resolves_relative_import_within_init_uses_own_package(
    tmp_path: Path,
) -> None:
    """`from .submodule import y` inside pkg/sub/__init__.py must resolve
    against pkg.sub's OWN dotted name (pkg.sub is already its own
    __package__), not its parent "pkg"."""
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "sub").mkdir()
    (tmp_path / "pkg" / "sub" / "__init__.py").write_text("from .submodule import y\n")
    (tmp_path / "pkg" / "sub" / "submodule.py").write_text("y = 1\n")

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    assert graph.has_edge("pkg.sub", "pkg.sub.submodule")
    assert graph.get_edge_data("pkg.sub", "pkg.sub.submodule")["kind"] == "imports"
    # Must not be miscomputed against the parent package "pkg".
    assert not graph.has_edge("pkg.sub", "pkg.submodule")


def test_build_repo_graph_resolves_cross_file_method_calls(tmp_path: Path) -> None:
    """Regression test for the full_name qualification fix: a call to a
    method on an imported class must resolve to the nested-qualified
    node id (module.ClassName.method), not just module.method_name."""
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "tools.py").write_text(
        "class Foo:\n"
        "    def search(self, query: str) -> str:\n"
        "        return query\n"
    )
    (tmp_path / "main.py").write_text(
        "from pkg.tools import Foo\n\n"
        "def run_agent():\n"
        "    f = Foo()\n"
        "    return f.search('hello')\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    assert graph.has_edge("main.run_agent", "pkg.tools.Foo.search")
