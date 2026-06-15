"""Entry point for the research agent."""

import asyncio
import os

from dotenv import load_dotenv

from graph import build_workflow

load_dotenv()


async def run_agent(query: str) -> dict[str, str]:
    """Run the full research workflow for a query."""
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required")
    workflow = build_workflow().compile()
    result = await workflow.ainvoke({"query": query, "plan": "", "findings": [], "summary": ""})
    return {"summary": result["summary"]}


if __name__ == "__main__":
    print(asyncio.run(run_agent("What is DataRobot?")))
