"""Entry-point discovery -- must surface every plausible candidate, with
enough structure that a human can tell which one is the real interface.
"""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.probes.entry_points import find_entry_points

FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures"


def _repo(tmp_path: Path, **files: str) -> Path:
    for name, source in files.items():
        path = tmp_path / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return tmp_path


def test_langgraph_fixture_yields_the_full_signature() -> None:
    """The acceptance case: parameters, annotations, default, async, doc."""
    entries = find_entry_points(build_repo_graph(FIXTURES / "langgraph_research_agent"))

    run_agent = next(e for e in entries if e.function == "run_agent")
    assert run_agent.module == "main"
    assert run_agent.is_async is True
    assert run_agent.signature == (
        "(query: str, max_sources: int = 3) -> dict[str, str | list[str]]"
    )
    assert [(p.name, p.annotation, p.default) for p in run_agent.parameters] == [
        ("query", "str", None),
        ("max_sources", "int", "3"),
    ]
    assert run_agent.returns == "dict[str, str | list[str]]"
    assert run_agent.site.file.endswith("main.py")


def test_console_script_wins_and_is_labelled(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        **{
            "pyproject.toml": '[project]\nname = "x"\n[project.scripts]\nx = "cli:serve"\n',
            "cli.py": "def serve(port: int = 8000) -> None:\n    pass\n",
        },
    )

    entries = find_entry_points(build_repo_graph(repo))

    serve = next(e for e in entries if e.function == "serve")
    assert serve.confidence == "console_script"
    assert serve.signature == "(port: int = 8000) -> None"


def test_main_guard_target_is_labelled(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        **{
            "app.py": 'def start(x):\n    """Go."""\n\n\nif __name__ == "__main__":\n    start(1)\n'
        },
    )

    entries = find_entry_points(build_repo_graph(repo))

    start = next(e for e in entries if e.function == "start")
    assert start.confidence == "main_guard"
    assert start.docstring == "Go."
    assert [(p.name, p.annotation, p.default) for p in start.parameters] == [("x", None, None)]
    assert start.returns is None


def test_reports_every_candidate_not_just_the_winner(tmp_path: Path) -> None:
    """A migration that picks the wrong entry point ships the wrong
    interface, so the human has to see the alternatives.
    """
    repo = _repo(
        tmp_path,
        **{
            "main.py": "def run_agent(q: str) -> str:\n    return q\n",
            "other.py": "def invoke(q: str) -> str:\n    return q\n",
        },
    )

    entries = find_entry_points(build_repo_graph(repo))

    assert {e.function for e in entries} == {"run_agent", "invoke"}
    assert all(e.confidence == "heuristic" for e in entries)
    # The strongest candidate is reported first.
    assert entries[0].function == "run_agent"


def test_a_repo_with_no_entry_point_returns_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path, **{"lib.py": "def helper(x):\n    return x\n"})

    assert find_entry_points(build_repo_graph(repo)) == []


def test_a_method_entry_point_keeps_its_qualified_name(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        **{"agent.py": "class Agent:\n    async def run(self, q: str) -> str:\n        return q\n"},
    )

    entries = find_entry_points(build_repo_graph(repo))

    assert [e.function for e in entries] == ["Agent.run"]
    assert entries[0].is_async is True
    assert [p.name for p in entries[0].parameters] == ["self", "q"]


def test_varargs_and_keyword_only_parameters_are_reported(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        **{"main.py": "def main(*args, verbose: bool = False, **kwargs) -> None:\n    pass\n"},
    )

    entries = find_entry_points(build_repo_graph(repo))

    assert [p.name for p in entries[0].parameters] == ["*args", "verbose", "**kwargs"]
    assert entries[0].signature == "(*args, verbose: bool = False, **kwargs) -> None"


def test_the_same_function_is_not_reported_twice(tmp_path: Path) -> None:
    """`main` is both the console-script target and the guard target."""
    repo = _repo(
        tmp_path,
        **{
            "pyproject.toml": '[project]\nname = "x"\n[project.scripts]\nx = "main:main"\n',
            "main.py": (
                'def main() -> None:\n    pass\n\n\nif __name__ == "__main__":\n    main()\n'
            ),
        },
    )

    entries = find_entry_points(build_repo_graph(repo))

    assert [(e.function, e.confidence) for e in entries] == [("main", "console_script")]


def test_every_entry_point_carries_a_graph_node_id(tmp_path: Path) -> None:
    repo = _repo(tmp_path, **{"main.py": "def run_agent() -> None:\n    pass\n"})
    graph = build_repo_graph(repo)

    entries = find_entry_points(graph)

    assert entries[0].site.node_id in graph.graph
