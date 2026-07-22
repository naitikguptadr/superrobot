"""Search tool for research agent."""

from __future__ import annotations

from langchain_core.tools import tool


@tool
async def web_search(query: str, limit: int = 3) -> list[str]:
    """Search the web for sources."""
    return [f"https://example.com/{query.replace(' ', '-')}-{i}" for i in range(limit)]
