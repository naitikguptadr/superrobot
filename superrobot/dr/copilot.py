"""AI Copilot — stage-specific LLM insights."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from superrobot.dr.llm_gateway import LLMGateway

STAGE_PROMPTS = {
    "scan": "copilot_scan",
    "analyze": "copilot_analyze",
    "generate": "copilot_generate",
    "evaluate": "copilot_evaluate",
    "deploy": "copilot_deploy",
}


async def stream_copilot(stage: str, context: dict[str, object]) -> AsyncIterator[str]:
    """Stream copilot response for a pipeline stage."""
    prompt_name = STAGE_PROMPTS.get(stage, "copilot_scan")
    user_content = f"{stage} completed. Here is the current state:\n{json.dumps(context, indent=2)}"
    gw = LLMGateway()
    async for chunk in gw.stream_text(prompt_name, user_content):
        yield chunk


async def get_copilot_insight(stage: str, context: dict[str, object]) -> str:
    """Collect full copilot response (non-streaming fallback)."""
    parts: list[str] = []
    async for chunk in stream_copilot(stage, context):
        parts.append(chunk)
    return "".join(parts)
