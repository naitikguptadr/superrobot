"""Sample LangChain agent fixture."""

import os

from langchain_openai import ChatOpenAI


async def run_agent(query: str) -> dict[str, str]:
    api_key = os.getenv("OPENAI_API_KEY", "")
    client = ChatOpenAI(api_key=api_key)
    response = await client.ainvoke(query)
    return {"response": str(response)}
