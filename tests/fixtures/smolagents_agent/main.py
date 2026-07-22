"""Minimal SmolaAgents fixture for scanner detection."""

from __future__ import annotations

from smolagents import CodeAgent, DuckDuckGoSearchTool, HfApiModel


async def run_agent(query: str) -> dict[str, str]:
    agent = CodeAgent(tools=[DuckDuckGoSearchTool()], model=HfApiModel())
    result = agent.run(query)
    return {"response": str(result)}
