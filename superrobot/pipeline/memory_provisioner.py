"""Memory API space provisioning — idempotent ensure-space for Spec 06."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from superrobot.dr.memory_client import MemoryApiError, MemoryClient


@dataclass
class MemoryEnsureResult:
    """Result of ensuring a named memory space exists."""

    success: bool
    action: Literal["found", "created"] | None
    space_id: str | None
    error_message: str | None = None


async def ensure_space(
    name: str,
    *,
    endpoint: str,
    token: str,
    client: MemoryClient | None = None,
) -> MemoryEnsureResult:
    """Get-or-create a named Memory API space."""
    memory_client = client or MemoryClient(endpoint, token)
    try:
        existing = await memory_client.find_space_by_name(name)
        if existing:
            return MemoryEnsureResult(
                success=True, action="found", space_id=str(existing.get("id", ""))
            )
        created = await memory_client.create_space(name)
        return MemoryEnsureResult(
            success=True, action="created", space_id=str(created.get("id", ""))
        )
    except MemoryApiError as exc:
        return MemoryEnsureResult(success=False, action=None, space_id=None, error_message=str(exc))
