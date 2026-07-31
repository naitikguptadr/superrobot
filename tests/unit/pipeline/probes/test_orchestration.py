"""Orchestration topology discovery -- the topology we cannot read must be
reported as unreadable, never guessed and never dropped.
"""

from __future__ import annotations

from pathlib import Path

from superrobot.ir.model import TopologyKind
from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.probes.orchestration import find_orchestration

FIXTURES = Path(__file__).parent.parent.parent.parent / "fixtures"


def _repo(tmp_path: Path, source: str, name: str = "main.py") -> Path:
    (tmp_path / name).write_text(source)
    return tmp_path


def _find(path: Path):  # type: ignore[no-untyped-def]
    return find_orchestration(build_repo_graph(path))


# --------------------------------------------------------------------------
# LangGraph -- the acceptance fixture
# --------------------------------------------------------------------------


def test_reads_the_langgraph_fixture_topology() -> None:
    finding = _find(FIXTURES / "langgraph_research_agent")

    assert finding is not None
    assert finding.kind is TopologyKind.GRAPH
    assert finding.framework == "langgraph"
    assert [n.name for n in finding.nodes] == ["planner", "researcher", "writer", "START", "END"]
    assert [(e.source, e.target) for e in finding.edges] == [
        ("START", "planner"),
        ("planner", "researcher"),
        ("writer", "END"),
    ]


def test_resolves_langgraph_node_callables_to_fully_qualified_names() -> None:
    finding = _find(FIXTURES / "langgraph_research_agent")

    assert finding is not None
    callables = {n.name: n.callable for n in finding.nodes}
    assert callables["planner"] == "graph.planner"
    assert callables["researcher"] == "graph.researcher"
    assert callables["writer"] == "graph.writer"


def test_unreadable_conditional_branches_are_reported_not_invented() -> None:
    """The fixture's router returns string literals from its body. Those
    targets are not readable at the call site, so they must land in
    `unresolved` -- inventing them is the failure mode we are eliminating.
    """
    finding = _find(FIXTURES / "langgraph_research_agent")

    assert finding is not None
    assert any("route_after_research" in item for item in finding.unresolved)
    assert any("researcher" in item for item in finding.unresolved)
    # No edge was fabricated out of the router's return values.
    assert not any(e.condition for e in finding.edges)
    assert ("researcher", "writer") not in [(e.source, e.target) for e in finding.edges]


def test_every_finding_carries_provenance() -> None:
    finding = _find(FIXTURES / "langgraph_research_agent")

    assert finding is not None
    assert finding.site.file.endswith("graph.py")
    assert finding.site.line > 0
    assert all(n.site.file.endswith("graph.py") for n in finding.nodes)
    assert all(e.site.line > 0 for e in finding.edges)


def test_reads_conditional_edges_when_a_path_map_is_given(tmp_path: Path) -> None:
    source = (
        "from langgraph.graph import END, START, StateGraph\n"
        "def route(state):\n"
        "    return 'a'\n"
        "def a(state):\n"
        "    return state\n"
        "def b(state):\n"
        "    return state\n"
        "g = StateGraph(dict)\n"
        "g.add_node('a', a)\n"
        "g.add_node('b', b)\n"
        "g.add_conditional_edges('a', route, {'yes': 'b', 'no': END})\n"
        "app = g.compile()\n"
    )
    finding = _find(_repo(tmp_path, source))

    assert finding is not None
    edges = {(e.source, e.target): e.condition for e in finding.edges}
    assert edges[("a", "b")] == "main.route"
    assert edges[("a", "END")] == "main.route"


def test_entry_and_finish_points_become_start_and_end_edges(tmp_path: Path) -> None:
    source = (
        "from langgraph.graph import StateGraph\n"
        "def a(state):\n"
        "    return state\n"
        "g = StateGraph(dict)\n"
        "g.add_node('a', a)\n"
        "g.set_entry_point('a')\n"
        "g.set_finish_point('a')\n"
    )
    finding = _find(_repo(tmp_path, source))

    assert finding is not None
    assert ("START", "a") in [(e.source, e.target) for e in finding.edges]
    assert ("a", "END") in [(e.source, e.target) for e in finding.edges]


def test_does_not_match_a_topology_written_inside_a_string(tmp_path: Path) -> None:
    assert _find(_repo(tmp_path, 'doc = "g = StateGraph(dict); g.add_node(1)"\n')) is None


# --------------------------------------------------------------------------
# CrewAI
# --------------------------------------------------------------------------


def test_reads_the_crewai_fixture_topology() -> None:
    finding = _find(FIXTURES / "crewai_agent")

    assert finding is not None
    assert finding.kind is TopologyKind.CREW
    assert finding.framework == "crewai"
    assert {n.name for n in finding.nodes} == {"Researcher", "task"}
    assert ("task", "Researcher") in [(e.source, e.target) for e in finding.edges]


def test_crewai_task_order_is_read_from_the_crew(tmp_path: Path) -> None:
    source = (
        "from crewai import Agent, Crew, Task\n"
        "a1 = Agent(role='One', goal='g')\n"
        "a2 = Agent(role='Two', goal='g')\n"
        "t1 = Task(description='first', agent=a1)\n"
        "t2 = Task(description='second', agent=a2)\n"
        "crew = Crew(agents=[a1, a2], tasks=[t1, t2])\n"
    )
    finding = _find(_repo(tmp_path, source))

    assert finding is not None
    pairs = [(e.source, e.target) for e in finding.edges]
    assert ("t1", "t2") in pairs
    assert ("t1", "One") in pairs
    assert ("t2", "Two") in pairs


def test_a_hierarchical_crew_does_not_get_invented_sequential_edges(tmp_path: Path) -> None:
    source = (
        "from crewai import Agent, Crew, Process, Task\n"
        "a1 = Agent(role='One', goal='g')\n"
        "t1 = Task(description='first', agent=a1)\n"
        "t2 = Task(description='second', agent=a1)\n"
        "crew = Crew(agents=[a1], tasks=[t1, t2], process=Process.hierarchical)\n"
    )
    finding = _find(_repo(tmp_path, source))

    assert finding is not None
    assert ("t1", "t2") not in [(e.source, e.target) for e in finding.edges]
    assert any("hierarchical" in item for item in finding.unresolved)


# --------------------------------------------------------------------------
# LlamaIndex
# --------------------------------------------------------------------------


def test_reads_the_llamaindex_fixture_as_a_sequential_pipeline() -> None:
    finding = _find(FIXTURES / "llamaindex_agent")

    assert finding is not None
    assert finding.kind is TopologyKind.SEQUENTIAL
    assert finding.framework == "llamaindex"
    assert [n.name for n in finding.nodes] == ["index", "engine"]
    assert [(e.source, e.target) for e in finding.edges] == [("index", "engine")]


# --------------------------------------------------------------------------
# The fallback: an unreadable topology is not the same as no topology
# --------------------------------------------------------------------------


def test_an_unreadable_framework_yields_unknown_not_none() -> None:
    finding = _find(FIXTURES / "autogen_agent")

    assert finding is not None
    assert finding.kind is TopologyKind.UNKNOWN
    assert finding.framework == "autogen"
    assert any("autogen" in item for item in finding.unresolved)


def test_no_framework_at_all_yields_none() -> None:
    assert _find(FIXTURES / "raw_async_agent") is None


def test_a_second_framework_is_recorded_rather_than_ignored(tmp_path: Path) -> None:
    (tmp_path / "flow.py").write_text(
        "from langgraph.graph import END, START, StateGraph\n"
        "def a(state):\n"
        "    return state\n"
        "g = StateGraph(dict)\n"
        "g.add_node('a', a)\n"
        "g.add_edge(START, 'a')\n"
        "g.add_edge('a', END)\n"
        "app = g.compile()\n"
    )
    (tmp_path / "crew.py").write_text(
        "from crewai import Agent, Crew, Task\n"
        "a1 = Agent(role='One', goal='g')\n"
        "t1 = Task(description='first', agent=a1)\n"
        "crew = Crew(agents=[a1], tasks=[t1])\n"
    )
    finding = _find(tmp_path)

    assert finding is not None
    assert finding.framework == "langgraph"
    assert any("crewai" in item for item in finding.unresolved)
