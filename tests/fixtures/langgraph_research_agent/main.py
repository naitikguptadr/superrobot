"""Research agent with LangGraph, tools, and multi-module layout."""

from __future__ import annotations

from graph import build_graph


async def run_agent(query: str, max_sources: int = 3) -> dict[str, str | list[str]]:
    graph = build_graph()
    state = await graph.ainvoke({"query": query, "max_sources": max_sources})
    return {
        "response": state.get("answer", ""),
        "sources": state.get("sources", []),
    }
