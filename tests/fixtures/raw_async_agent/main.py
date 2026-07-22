"""Sample raw async agent fixture."""

import httpx


async def run_agent(url: str) -> dict[str, str]:
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return {"response": response.text}
