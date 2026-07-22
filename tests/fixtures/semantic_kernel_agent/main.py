"""Minimal Semantic Kernel agent fixture for scanner detection."""

from __future__ import annotations

import os

from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion


async def run_agent(query: str) -> dict[str, str]:
    kernel = Kernel()
    kernel.add_service(
        OpenAIChatCompletion(
            ai_model_id="gpt-4o",
            api_key=os.getenv("OPENAI_API_KEY"),
        )
    )
    result = await kernel.invoke_prompt(query)
    return {"response": str(result)}
