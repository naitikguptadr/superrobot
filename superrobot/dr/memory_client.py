"""DataRobot Memory (Agentic Memory) API client — ensure named memory spaces."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeAlias

import httpx

from superrobot.setup.endpoints import api_endpoint

Transport: TypeAlias = Callable[
    [str, str, dict[str, str], object | None], Awaitable[tuple[int, object]]
]


class MemoryApiError(RuntimeError):
    """Memory API request failed."""


class MemoryClient:
    """Async client for the DataRobot Agentic Memory API."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        transport: Transport | None = None,
    ) -> None:
        self._base = f"{api_endpoint(endpoint)}/genai/agenticMemory/spaces"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._transport = transport or _http_transport

    async def find_space_by_name(self, name: str) -> dict[str, object] | None:
        """Look up a memory space by name; None if it does not exist yet."""
        status, body = await self._transport(
            "GET", f"{self._base}/?name={name}", self._headers, None
        )
        if status != 200 or not isinstance(body, dict):
            return None
        for item in body.get("data", []):
            if isinstance(item, dict) and item.get("name") == name:
                return item
        return None

    async def create_space(self, name: str) -> dict[str, object]:
        status, body = await self._transport(
            "POST", f"{self._base}/", self._headers, {"name": name}
        )
        if not 200 <= status < 300 or not isinstance(body, dict):
            raise MemoryApiError(f"Memory space create failed ({status}): {body}")
        return body


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
