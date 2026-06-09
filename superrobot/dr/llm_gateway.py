"""AsyncOpenAI client for DR LLM Gateway with retry logic."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TypeVar

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "azure/gpt-4o-2024-11-20"
MAX_RETRIES = 3
BASE_DELAY = 2.0


class LLMOutputValidationError(Exception):
    """Raised when LLM output fails Pydantic validation after retry."""

    def __init__(self, message: str, raw_outputs: list[str]) -> None:
        super().__init__(message)
        self.raw_outputs = raw_outputs


class LLMGateway:
    """DR LLM Gateway client with retries and structured output validation."""

    def __init__(self, model: str | None = None) -> None:
        endpoint = os.environ.get("DATAROBOT_ENDPOINT", "")
        token = os.environ.get("DATAROBOT_API_TOKEN", "")
        self._model = model or os.environ.get("SUPERROBOT_MODEL", DEFAULT_MODEL)
        self._debug = os.environ.get("SUPERROBOT_DEBUG", "") == "1"
        self._client = AsyncOpenAI(
            base_url=f"{endpoint}/api/v2/genai/llmgw",
            api_key=token,
        )

    def load_prompt(self, name: str) -> str:
        """Load a prompt template from superrobot/dr/prompts/."""
        path = Path(__file__).parent / "prompts" / f"{name}.txt"
        return path.read_text()

    async def call(
        self,
        prompt_name: str,
        user_content: str,
        response_model: type[T],
        extra_system: str = "",
    ) -> T:
        """Call LLM Gateway with prompt template and validate response."""
        system = self.load_prompt(prompt_name)
        if extra_system:
            system = f"{system}\n\n{extra_system}"
        raw_outputs: list[str] = []
        last_error: ValidationError | json.JSONDecodeError | None = None

        for attempt in range(MAX_RETRIES + 1):
            content = user_content
            if last_error is not None:
                content = f"{user_content}\n\nPrevious validation error:\n{last_error}"

            try:
                raw = await self._chat(system, content)
                raw_outputs.append(raw)
                data = json.loads(raw)
                return response_model.model_validate(data)
            except (ValidationError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(BASE_DELAY * (2**attempt))
                    continue
                raise LLMOutputValidationError(
                    f"LLM output validation failed after {MAX_RETRIES + 1} attempts: {exc}",
                    raw_outputs,
                ) from exc
            except Exception:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(BASE_DELAY * (2**attempt))
                    continue
                raise

        raise LLMOutputValidationError("Unreachable", raw_outputs)

    async def call_text(self, prompt_name: str, user_content: str) -> str:
        """Call LLM Gateway and return raw text (no JSON validation)."""
        system = self.load_prompt(prompt_name)
        for attempt in range(MAX_RETRIES):
            try:
                return await self._chat(system, user_content, json_mode=False)
            except Exception:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(BASE_DELAY * (2**attempt))
                    continue
                raise
        return ""

    async def _chat(self, system: str, user: str, *, json_mode: bool = True) -> str:
        if self._debug:
            import sys

            print(f"[LLM] model={self._model}", file=sys.stderr)
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if json_mode:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_format={"type": "json_object"},
            )
        else:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        content = response.choices[0].message.content or ""
        return content.strip()
