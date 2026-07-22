"""Minimal AutoGen-style agent fixture for scanner detection."""

from __future__ import annotations

import os

from autogen import AssistantAgent, UserProxyAgent


async def run_agent(query: str) -> dict[str, str]:
    assistant = AssistantAgent(
        name="assistant",
        llm_config={"model": "gpt-4o", "api_key": os.getenv("OPENAI_API_KEY")},
    )
    user = UserProxyAgent(name="user", human_input_mode="NEVER")
    user.initiate_chat(assistant, message=query)
    return {"response": str(assistant.last_message())}
