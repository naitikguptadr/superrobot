"""LangGraph workflow for research agent."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from tools.search import web_search


def planner(state: dict) -> dict:
    return {**state, "plan": f"research: {state['query']}"}


async def researcher(state: dict) -> dict:
    hits = await web_search(state["query"], limit=state.get("max_sources", 3))
    return {**state, "sources": hits}


def writer(state: dict) -> dict:
    sources = state.get("sources", [])
    answer = f"Synthesized answer for {state['query']} using {len(sources)} sources"
    return {**state, "answer": answer}


def route_after_research(state: dict) -> str:
    if state.get("sources"):
        return "writer"
    return "researcher"


def build_graph():
    graph = StateGraph(dict)
    graph.add_node("planner", planner)
    graph.add_node("researcher", researcher)
    graph.add_node("writer", writer)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "researcher")
    graph.add_conditional_edges("researcher", route_after_research)
    graph.add_edge("writer", END)
    return graph.compile()
