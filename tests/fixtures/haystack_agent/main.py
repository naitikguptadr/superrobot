"""Minimal Haystack pipeline fixture for scanner detection."""

from __future__ import annotations

from haystack import Pipeline
from haystack.components.builders import PromptBuilder
from haystack.components.generators import OpenAIGenerator


async def run_agent(query: str) -> dict[str, str]:
    pipe = Pipeline()
    pipe.add_component("prompt", PromptBuilder(template="Answer: {{query}}"))
    pipe.add_component("llm", OpenAIGenerator(model="gpt-4o"))
    pipe.connect("prompt", "llm")
    result = pipe.run({"prompt": {"query": query}})
    return {"response": str(result)}
