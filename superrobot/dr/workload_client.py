"""DataRobot Workload API client — create/replace containerized workloads."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeAlias

import httpx

from superrobot.setup.endpoints import api_endpoint

Transport: TypeAlias = Callable[
    [str, str, dict[str, str], object | None], Awaitable[tuple[int, object]]
]


class WorkloadApiError(RuntimeError):
    """Workload API request failed."""


class WorkloadClient:
    """Async client for the DataRobot Workload API."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        transport: Transport | None = None,
    ) -> None:
        self._base = f"{api_endpoint(endpoint)}/workloads"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._transport = transport or _http_transport

    async def find_by_name(self, name: str) -> dict[str, object] | None:
        """Look up a live workload by name; None if it does not exist yet."""
        status, body = await self._transport(
            "GET", f"{self._base}/?name={name}", self._headers, None
        )
        if status != 200 or not isinstance(body, dict):
            return None
        for item in body.get("data", []):
            if isinstance(item, dict) and item.get("name") == name:
                return item
        return None

    async def create(self, manifest: dict[str, object]) -> dict[str, object]:
        status, body = await self._transport("POST", f"{self._base}/", self._headers, manifest)
        if not 200 <= status < 300 or not isinstance(body, dict):
            raise WorkloadApiError(f"Workload create failed ({status}): {body}")
        return body

    async def replace(self, workload_id: str, manifest: dict[str, object]) -> dict[str, object]:
        """Rolling-replace a live workload's artifact/runtime spec."""
        status, body = await self._transport(
            "PATCH", f"{self._base}/{workload_id}/", self._headers, manifest
        )
        if not 200 <= status < 300 or not isinstance(body, dict):
            raise WorkloadApiError(f"Workload replace failed ({status}): {body}")
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
