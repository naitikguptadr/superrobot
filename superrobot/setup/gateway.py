"""DataRobot LLM Gateway client — product model path."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import TypeAlias

import httpx

from superrobot.setup.endpoints import gateway_base_url

Transport: TypeAlias = Callable[
    [str, str, dict[str, str], object | None], Awaitable[tuple[int, object]]
]


class GatewayError(RuntimeError):
    """LLM Gateway request failure with redacted detail."""


async def verify_gateway(
    endpoint: str,
    token: str,
    *,
    model: str | None = None,
    transport: Transport | None = None,
) -> bool:
    """Ping Gateway models listing (or chat) to confirm credentials work."""
    base = gateway_base_url(endpoint)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    runner = transport or _http_transport
    # DataRobot's LLM Gateway is not OpenAI-hosted -- it has no /v1 prefix.
    # The documented, token-authenticated paths are /catalog/ (model listing)
    # and /chat/completions/ (confirmed against a real staging environment;
    # /v1/models and /v1/chat/completions both 404 there).
    status, payload = await runner("GET", f"{base}/catalog/", headers, None)
    if status == 404:
        # Some gateways only expose chat; fall back to a minimal models-compatible probe
        selected = model or os.environ.get("SUPERROBOT_MODEL", "azure/gpt-5-5-2026-04-23")
        status, payload = await runner(
            "POST",
            f"{base}/chat/completions/",
            headers,
            {
                "model": selected,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
        )
    if not 200 <= status < 300:
        detail = _redact(str(payload), token)
        raise GatewayError(f"LLM Gateway verify failed ({status}): {detail}")
    return True


async def _http_transport(
    method: str, url: str, headers: dict[str, str], payload: object | None
) -> tuple[int, object]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(method, url, headers=headers, json=payload)
        try:
            body: object = response.json()
        except ValueError:
            body = {"detail": response.text}
        return response.status_code, body


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text
