"""Web search tool."""

import os

import httpx
from langchain_core.tools import tool


@tool
async def web_search(query: str) -> str:
    """Search the web and return top results."""
    api_key = os.getenv("SERPAPI_API_KEY", "")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://serpapi.com/search",
            params={"q": query, "api_key": api_key},
        )
    return resp.text[:2000]


@tool
def fetch_page(url: str) -> str:
    """Fetch a web page's text content."""
    resp = httpx.get(url, timeout=10)
    return resp.text[:5000]
