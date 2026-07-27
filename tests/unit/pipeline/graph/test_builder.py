"""Tests for RepoGraph construction, save, and load."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    (tmp_path / "pkg" / "tools.py").write_text("def search(query: str) -> str:\n    return query\n")
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
    (tmp_path / "pkg" / "tools.py").write_text("def search(query: str) -> str:\n    return query\n")
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


def test_build_repo_graph_tags_type_checking_only_import(tmp_path: Path) -> None:
    """An import that lives only inside `if TYPE_CHECKING:` never executes
    at runtime, so its "imports" edge must be tagged distinctly from a
    real, executed import."""
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "main.py").write_text(
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n"
        "    from crewai import Agent\n\n"
        "def run():\n"
        "    pass\n"
    )

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    assert graph.has_edge("main", "crewai")
    edge_data = graph.get_edge_data("main", "crewai")
    assert edge_data["kind"] == "imports"
    assert edge_data["type_checking_only"] is True


def test_build_repo_graph_does_not_tag_normal_import_as_type_checking_only(
    tmp_path: Path,
) -> None:
    """A normal, non-guarded import must NOT carry the type_checking_only
    marker -- only imports found inside an `if TYPE_CHECKING:` block do."""
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "main.py").write_text("from crewai import Agent\n\ndef run():\n    pass\n")

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    assert graph.has_edge("main", "crewai")
    edge_data = graph.get_edge_data("main", "crewai")
    assert edge_data["kind"] == "imports"
    assert not edge_data.get("type_checking_only", False)


def test_build_repo_graph_real_import_wins_when_type_checking_import_comes_first(
    tmp_path: Path,
) -> None:
    """A module imported both inside `if TYPE_CHECKING:` and for real (at
    runtime) must NOT be marked type_checking_only -- the real import
    always wins, regardless of ast.walk() traversal order. This variant
    puts the TYPE_CHECKING-guarded import first in source order, the
    layout most likely to trigger an order-dependent edge-attribute bug
    (since add_edge() on an existing edge overwrites its attributes)."""
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "main.py").write_text(
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n"
        "    import json\n\n"
        "import json\n\n"
        "def run():\n"
        "    pass\n"
    )

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    assert graph.has_edge("main", "json")
    edge_data = graph.get_edge_data("main", "json")
    assert edge_data["kind"] == "imports"
    assert not edge_data.get("type_checking_only", False)


def test_build_repo_graph_real_import_wins_when_type_checking_import_comes_second(
    tmp_path: Path,
) -> None:
    """Same scenario as above but with the real import appearing first and
    the TYPE_CHECKING-guarded duplicate second, to cover both possible
    traversal orderings."""
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "main.py").write_text(
        "import json\n\n"
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n"
        "    import json\n\n"
        "def run():\n"
        "    pass\n"
    )

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    assert graph.has_edge("main", "json")
    edge_data = graph.get_edge_data("main", "json")
    assert edge_data["kind"] == "imports"
    assert not edge_data.get("type_checking_only", False)


def test_build_repo_graph_finds_files_under_dot_prefixed_ancestor_dir(tmp_path: Path) -> None:
    """iter_python_files()/build_repo_graph() must check paths RELATIVE to
    repo_root, not the full absolute path -- a dot-prefixed ANCESTOR
    directory above repo_root (e.g. a repo living under ~/.cache/... on a
    CI runner) must not cause every file to look "hidden" and be
    silently excluded, leaving an empty graph."""
    from superrobot.pipeline.graph.builder import build_repo_graph

    repo_root = tmp_path / ".hidden_ancestor" / "myrepo"
    (repo_root / "pkg").mkdir(parents=True)
    (repo_root / "pkg" / "__init__.py").write_text("")
    (repo_root / "pkg" / "tools.py").write_text(
        "def search(query: str) -> str:\n    return query\n"
    )
    (repo_root / "main.py").write_text("def run_agent():\n    return 'ok'\n")

    repo_graph = build_repo_graph(repo_root)
    graph = repo_graph.graph

    assert graph.number_of_nodes() > 0
    assert graph.nodes["main"]["kind"] == "module"
    assert graph.nodes["pkg.tools"]["kind"] == "module"
    assert graph.nodes["pkg.tools.search"]["kind"] == "function"


def test_build_repo_graph_does_not_attribute_nested_function_calls_to_outer_function(
    tmp_path: Path,
) -> None:
    """A call made inside a nested inner function must be attributed only
    to that inner function, never spuriously also to every enclosing outer
    function -- ast.walk(outer_func_node) yields everything inside a
    nested inner function too, so naively walking each function's full
    subtree double (or triple, etc.) counts calls made only by inner
    functions never actually invoked by the outer function's own code."""
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "main.py").write_text(
        "def helper():\n"
        "    return 1\n\n"
        "def outer():\n"
        "    def inner():\n"
        "        return helper()\n"
        "    return inner\n"
    )

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    assert not graph.has_edge("main.outer", "main.helper")
    assert graph.has_edge("main.outer.inner", "main.helper")
    assert graph.get_edge_data("main.outer.inner", "main.helper")["kind"] == "calls"


def test_build_repo_graph_skips_broken_symlink_without_raising(tmp_path: Path) -> None:
    """A broken/dangling symlink anywhere in the scanned tree is matched by
    rglob("*.py") by name pattern alone, without checking it resolves.
    Reading it raises FileNotFoundError (an OSError subclass), which must
    be caught and skipped just like a SyntaxError/UnicodeDecodeError,
    rather than crashing the whole build_repo_graph() call for the entire
    repo over one unrelated broken symlink."""
    import os

    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "main.py").write_text("def run_agent():\n    return 'ok'\n")
    os.symlink(tmp_path / "does_not_exist.py", tmp_path / "broken_link.py")

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    assert graph.nodes["main"]["kind"] == "module"
    assert "broken_link" not in graph.nodes


def test_build_repo_graph_disambiguates_module_and_same_named_function_in_init(
    tmp_path: Path,
) -> None:
    """A package's __init__.py can define a function/class whose name
    matches a sibling submodule (e.g. `def b(): ...` in pkg/__init__.py
    vs. a sibling module pkg/b.py) -- module_dotted_name() strips the
    "__init__" segment, so both would naively compute the exact same bare
    node id "pkg.b". graph.add_node()'s attribute-merge behavior means
    whichever gets processed second silently overwrites the other's
    kind/path/line, erasing one of the two entities from the graph with no
    error. They must end up as two DISTINCT nodes with correct `kind`
    attributes.

    The module keeps its plain id "pkg.b" (kind="module"); the colliding
    function resolves to the disambiguated id "pkg.b:obj" (kind="function")
    -- see builder.code_object_node_id's docstring for why.
    """
    from superrobot.pipeline.graph.builder import build_repo_graph, code_object_node_id

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("def b():\n    return 1\n")
    (tmp_path / "pkg" / "b.py").write_text("VAL = 42\n")

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    # The module "pkg.b" (from pkg/b.py) must exist, untouched, under its
    # plain dotted id.
    assert graph.nodes["pkg.b"]["kind"] == "module"

    # The function "b" defined in pkg/__init__.py must ALSO exist, under
    # its disambiguated id -- not silently missing, and not merged into
    # the module node.
    disambiguated_id = code_object_node_id("pkg.b", graph)
    assert disambiguated_id != "pkg.b"
    assert disambiguated_id in graph
    assert graph.nodes[disambiguated_id]["kind"] == "function"
    assert graph.nodes[disambiguated_id]["line"] == 1

    # The "defines" edge from the package module ("pkg") to the function
    # must reference the disambiguated id, not the (module-owned) bare id.
    assert graph.has_edge("pkg", disambiguated_id)
    assert graph.get_edge_data("pkg", disambiguated_id)["kind"] == "defines"


def test_build_repo_graph_resolves_cross_file_method_calls(tmp_path: Path) -> None:
    """Regression test for the full_name qualification fix: a call to a
    method on an imported class must resolve to the nested-qualified
    node id (module.ClassName.method), not just module.method_name."""
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "tools.py").write_text(
        "class Foo:\n    def search(self, query: str) -> str:\n        return query\n"
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


def test_build_repo_graph_resolves_call_edges_for_a_relative_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """build_repo_graph() must behave identically whether repo_root is
    given as an absolute or a relative path.

    jedi's `Definition.module_path` is always absolute, and pass 2 keeps a
    call target only if `target_path.is_relative_to(repo_root)`. A
    relative repo_root makes that check compare an absolute path against a
    relative one, which is False for EVERY target -- so the whole cross-
    file call graph is silently dropped and the graph ends up with zero
    "calls" edges, with no error anywhere.

    That used to be invisible because nothing consumed `calls` edges:
    resolve_entry_point() returned None for any repo without a
    __main__ guard/console script, and detect_framework() treats an
    unresolved entry point as "everything is reachable". Now that the
    tier-3 name heuristic resolves an entry point for ordinary agent
    repos, those edges drive real reachability, and losing them silently
    downgrades a correct detection into an "unreachable framework
    import" false positive. scanner.scan() already normalizes with
    Path(repo_path).resolve() for the same reason.
    """
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "tools.py").write_text("def search(query):\n    return query\n")
    (tmp_path / "main.py").write_text(
        "from pkg.tools import search\n\ndef run_agent():\n    return search('hi')\n"
    )

    absolute_graph = build_repo_graph(tmp_path)
    monkeypatch.chdir(tmp_path)
    relative_graph = build_repo_graph(Path("."))

    assert absolute_graph.graph.has_edge("main.run_agent", "pkg.tools.search")
    assert relative_graph.graph.has_edge("main.run_agent", "pkg.tools.search")


def test_build_repo_graph_call_prefilter_keeps_repo_calls_and_ignores_stdlib_calls(
    tmp_path: Path,
) -> None:
    """Pass 2 skips jedi inference for any called name that could not match
    a graph node. Both halves of that pre-filter's contract are pinned
    here: a call to a name that IS defined in the repo must still produce
    its "calls" edge, and a call to a stdlib/unknown name must still
    produce none -- the filter is a pure speedup, so the resulting edge set
    has to be exactly what it was when every call site was inferred.
    """
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "tools.py").write_text("def search(query):\n    return query\n")
    (tmp_path / "main.py").write_text(
        "from pkg.tools import search\n\n"
        "def run_agent(items):\n"
        "    print(len(items))\n"
        "    return search('hi')\n"
    )

    graph = build_repo_graph(tmp_path).graph

    calls_from_run_agent = {
        target
        for _, target, data in graph.out_edges("main.run_agent", data=True)
        if data.get("kind") == "calls"
    }
    # The in-repo call survives the pre-filter...
    assert calls_from_run_agent == {"pkg.tools.search"}
    # ...and the stdlib calls contribute nothing at all.
    assert not graph.has_node("print")
    assert not graph.has_node("len")


def test_build_repo_graph_resolves_call_made_through_an_import_alias(tmp_path: Path) -> None:
    """A call site can name its target through an alias, so the pass-2
    pre-filter cannot key purely off the names of definitions in the repo.

    `from pkg.tools import search as find` means the call reads `find(...)`
    while the definition -- and therefore the graph node id -- is
    `pkg.tools.search`. Filtering on the syntactic name alone would skip
    the inference and silently drop this real edge.
    """
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "tools.py").write_text("def search(query):\n    return query\n")
    (tmp_path / "main.py").write_text(
        "from pkg.tools import search as find\n\ndef run_agent():\n    return find('hi')\n"
    )

    graph = build_repo_graph(tmp_path).graph

    assert graph.has_edge("main.run_agent", "pkg.tools.search")
    assert graph.get_edge_data("main.run_agent", "pkg.tools.search")["kind"] == "calls"


def test_build_repo_graph_resolves_cls_call_inside_a_classmethod(tmp_path: Path) -> None:
    """`return cls(...)` in a classmethod is the most common aliased call
    in real code -- the syntactic name is `cls`, but it resolves to the
    enclosing class. The pass-2 pre-filter must let it through.
    """
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "models.py").write_text(
        "class Config:\n    @classmethod\n    def load(cls):\n        return cls()\n"
    )

    graph = build_repo_graph(tmp_path).graph

    assert graph.has_edge("models.Config.load", "models.Config")
    assert graph.get_edge_data("models.Config.load", "models.Config")["kind"] == "calls"
