"""AsyncOpenAI client for DR LLM Gateway with retry logic."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TypeVar

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "azure/gpt-5-5-2026-04-23"
MAX_RETRIES = 3
BASE_DELAY = 2.0


def has_llm_credentials() -> bool:
    """True when DR platform endpoint and API token are configured."""
    endpoint = os.environ.get("DATAROBOT_ENDPOINT", "").strip()
    token = os.environ.get("DATAROBOT_API_TOKEN", "").strip()
    return bool(endpoint and token)


class LLMCredentialsError(Exception):
    """Raised when LLM Gateway is used without credentials."""


class LLMOutputValidationError(Exception):
    """Raised when LLM output fails Pydantic validation after retry."""

    def __init__(self, message: str, raw_outputs: list[str]) -> None:
        super().__init__(message)
        self.raw_outputs = raw_outputs


class LLMGateway:
    """DR LLM Gateway client with retries and structured output validation."""

    def __init__(self, model: str | None = None) -> None:
        from superrobot.setup.constants import normalize_endpoint

        self._endpoint = normalize_endpoint(os.environ.get("DATAROBOT_ENDPOINT", ""))
        self._token = os.environ.get("DATAROBOT_API_TOKEN", "").strip()
        self._model = model or os.environ.get("SUPERROBOT_MODEL", DEFAULT_MODEL)
        self._debug = os.environ.get("SUPERROBOT_DEBUG", "") == "1"
        self._client: AsyncOpenAI | None = None
        if has_llm_credentials():
            self._client = AsyncOpenAI(
                base_url=f"{self._endpoint}/api/v2/genai/llmgw",
                api_key=self._token,
            )

    @property
    def available(self) -> bool:
        """Whether the gateway client was constructed with credentials."""
        return self._client is not None

    def _require_client(self) -> AsyncOpenAI:
        if self._client is None:
            raise LLMCredentialsError(
                "DATAROBOT_ENDPOINT and DATAROBOT_API_TOKEN are required for LLM calls. "
                "Run: superrobot setup"
            )
        return self._client

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

    async def ping(self) -> bool:
        """Minimal gateway connectivity check."""
        if self._client is None:
            return False
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": "Reply with OK"}],
                max_tokens=5,
            )
            return bool(response.choices[0].message.content)
        except Exception:
            return False

    async def stream_text(self, prompt_name: str, user_content: str) -> AsyncIterator[str]:
        """Stream raw text from LLM Gateway."""
        client = self._require_client()
        system = self.load_prompt(prompt_name)
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        stream = await client.chat.completions.create(
            model=self._model,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

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
        client = self._require_client()
        if json_mode:
            response = await client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_format={"type": "json_object"},
            )
        else:
            response = await client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        content = response.choices[0].message.content or ""
        return content.strip()
