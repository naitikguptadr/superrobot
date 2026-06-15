"""LangGraph workflow definition."""

from typing import TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from tools.search import fetch_page, web_search


class ResearchState(TypedDict):
    query: str
    plan: str
    findings: list[str]
    summary: str


llm = ChatOpenAI(model="gpt-4o", temperature=0)


async def planner(state: ResearchState) -> ResearchState:
    plan = await llm.ainvoke(f"Make a research plan for: {state['query']}")
    return {**state, "plan": str(plan.content)}


async def researcher(state: ResearchState) -> ResearchState:
    results = await web_search.ainvoke(state["plan"])
    return {**state, "findings": [results]}


async def writer(state: ResearchState) -> ResearchState:
    summary = await llm.ainvoke(f"Summarize: {state['findings']}")
    return {**state, "summary": str(summary.content)}


def should_continue(state: ResearchState) -> str:
    return "writer" if state["findings"] else "researcher"


def build_workflow() -> StateGraph:
    g = StateGraph(ResearchState)
    g.add_node("planner", planner)
    g.add_node("researcher", researcher)
    g.add_node("writer", writer)
    g.add_edge(START, "planner")
    g.add_edge("planner", "researcher")
    g.add_conditional_edges("researcher", should_continue)
    g.add_edge("writer", END)
    return g
