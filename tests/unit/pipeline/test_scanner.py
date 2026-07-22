"""Scanner unit tests."""

from pathlib import Path

import pytest

from superrobot.pipeline.scanner import scan

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def test_scan_langchain_agent_happy_path() -> None:
    result = scan(FIXTURES / "langchain_agent")
    assert result.detected_framework in ("langchain", "langgraph")
    assert result.confidence >= 0.5
    assert any(ep.function == "run_agent" for ep in result.entry_points)
    assert "OPENAI_API_KEY" in result.env_vars
    assert "langchain" in result.dependencies or "langchain-openai" in result.dependencies


def test_scan_crewai_agent() -> None:
    result = scan(FIXTURES / "crewai_agent")
    assert result.detected_framework == "crewai"
    assert result.confidence >= 0.8


def test_scan_missing_path_raises() -> None:
    with pytest.raises(FileNotFoundError):
        scan("/nonexistent/path/to/repo")


def test_scan_raw_async_low_confidence() -> None:
    result = scan(FIXTURES / "raw_async_agent")
    assert result.detected_framework == "raw_async"
    assert result.confidence <= 0.6


def test_scan_llamaindex_agent() -> None:
    result = scan(FIXTURES / "llamaindex_agent")
    assert result.detected_framework == "llamaindex"


def test_scan_autogen_agent() -> None:
    result = scan(FIXTURES / "autogen_agent")
    assert result.detected_framework == "autogen"
    assert result.confidence >= 0.7


def test_scan_semantic_kernel_agent() -> None:
    result = scan(FIXTURES / "semantic_kernel_agent")
    assert result.detected_framework == "semantic_kernel"
    assert result.confidence >= 0.7


def test_scan_haystack_agent() -> None:
    result = scan(FIXTURES / "haystack_agent")
    assert result.detected_framework == "haystack"
    assert result.confidence >= 0.7


def test_scan_smolagents_agent() -> None:
    result = scan(FIXTURES / "smolagents_agent")
    assert result.detected_framework == "smolagents"
    assert result.confidence >= 0.7


def test_scan_langgraph_research_agent_complex() -> None:
    result = scan(FIXTURES / "langgraph_research_agent")
    assert result.detected_framework == "langgraph"
    assert result.has_state_graph is True
    assert result.primary_entry is not None
    assert result.primary_entry.function == "run_agent"
    assert "web_search" in result.tools
    assert len(result.graph_nodes) >= 5
    assert any(n["id"] == "planner" for n in result.graph_nodes)
    assert result.python_file_count == 3


def test_build_graph_langchain_fixture_is_clean() -> None:
    """Regression: graph used to include every AST call (getenv, ainvoke, str…)."""
    from superrobot.pipeline.scanner import build_graph

    nodes, edges = build_graph("tests/fixtures/langchain_agent")
    labels = [n["label"] for n in nodes]
    assert labels == ["Input", "run_agent()", "ChatOpenAI", "Output"]
    assert ("input", "run_agent") in edges
    assert ("run_agent", "ChatOpenAI") in edges
    assert ("ChatOpenAI", "output") in edges


def test_build_graph_extracts_real_stategraph(tmp_path) -> None:
    (tmp_path / "graph_agent.py").write_text(
        "from langgraph.graph import StateGraph, START, END\n"
        "g = StateGraph(dict)\n"
        'g.add_node("planner", lambda s: s)\n'
        'g.add_node("writer", lambda s: s)\n'
        'g.add_edge(START, "planner")\n'
        'g.add_edge("planner", "writer")\n'
        'g.add_edge("writer", END)\n'
        'g.add_conditional_edges("planner", lambda s: "writer")\n'
    )
    from superrobot.pipeline.scanner import build_graph

    nodes, edges = build_graph(tmp_path)
    by_id = {n["id"]: n for n in nodes}
    assert "planner" in by_id and "writer" in by_id
    assert by_id["planner"]["type"] == "router"  # has conditional edges
    assert ("input", "planner") in edges
    assert ("planner", "writer") in edges
    assert ("writer", "output") in edges


def test_build_graph_resolves_conditional_edge_targets(tmp_path) -> None:
    """Conditional routing functions' return constants become real edges."""
    (tmp_path / "agent.py").write_text(
        "from langgraph.graph import StateGraph, START, END\n"
        "def route(state):\n"
        '    if state.get("done"):\n'
        '        return "writer"\n'
        '    return "researcher"\n'
        "g = StateGraph(dict)\n"
        'g.add_node("planner", lambda s: s)\n'
        'g.add_node("researcher", lambda s: s)\n'
        'g.add_node("writer", lambda s: s)\n'
        'g.add_edge(START, "planner")\n'
        'g.add_edge("planner", "researcher")\n'
        'g.add_conditional_edges("researcher", route)\n'
        'g.add_edge("writer", END)\n'
    )
    from superrobot.pipeline.scanner import build_graph

    nodes, edges = build_graph(tmp_path)
    assert ("researcher", "writer") in edges
    by_id = {n["id"]: n for n in nodes}
    assert by_id["researcher"]["type"] == "router"
