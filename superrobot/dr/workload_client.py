"""DataRobot Workload API client — create/replace containerized workloads."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeAlias
from urllib.parse import quote

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
        """Look up a live workload by name.

        Returns None only when the workload genuinely is not there. A failed
        lookup raises instead: treating a 401/429/500 as "does not exist"
        made `deploy_workload` take its create branch, which skipped the
        replica preflight guard and tried to create a duplicate -- surfacing
        to the user as a confusing 409 name conflict rather than the auth or
        outage that actually happened.
        """
        status, body = await self._transport(
            "GET", f"{self._base}/?name={quote(name, safe='')}", self._headers, None
        )
        if status == 404:
            return None
        if not 200 <= status < 300:
            raise WorkloadApiError(f"Workload lookup failed ({status}): {body}")
        if not isinstance(body, dict):
            raise WorkloadApiError(f"Workload lookup returned an unexpected body: {body!r}")
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
        """Roll a live workload onto a different artifact.

        Uses `POST /workloads/{id}/replacement/`. This previously sent
        `PATCH /workloads/{id}/`, which DataRobot documents as "Metadata only
        -- no restart" (see the disambiguation table in
        vendor/datarobot-agent-skills/skills/datarobot-workload-api/SKILL.md).
        The call succeeded, so every deploy after the first reported
        `action="replaced"` while the workload kept serving the old image.

        Requires an `artifactId`. A bring-your-own-image manifest carries an
        inline `artifact` spec instead, which this endpoint cannot consume --
        that raises rather than silently leaving the old image in place.
        """
        artifact_id = manifest.get("artifactId")
        if not artifact_id:
            raise WorkloadApiError(
                "Rolling replacement requires an artifactId, but this manifest carries an "
                "inline artifact spec (bring-your-own-image). Build the image into an "
                "artifact first and redeploy with --artifact-id."
            )

        payload: dict[str, object] = {"artifactId": artifact_id, "strategy": "rolling"}
        runtime = manifest.get("runtime")
        if runtime is not None:
            payload["runtime"] = runtime

        status, body = await self._transport(
            "POST", f"{self._base}/{workload_id}/replacement/", self._headers, payload
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
