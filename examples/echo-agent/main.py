"""Minimal raw-async agent — answers by transforming the query (stdlib only)."""

import asyncio


async def run_agent(query: str) -> dict:
    """Answer a query with a deterministic transformation."""
    await asyncio.sleep(0)
    return {"response": f"Echo agent received: {query}"}


if __name__ == "__main__":
    print(asyncio.run(run_agent("hello")))
