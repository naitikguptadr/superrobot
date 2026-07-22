"""Memory space provisioning unit tests."""

from __future__ import annotations

import asyncio

from superrobot.dr.memory_client import MemoryApiError
from superrobot.pipeline.memory_provisioner import ensure_space


class _FakeClient:
    def __init__(
        self, existing: dict[str, object] | None = None, create_error: Exception | None = None
    ) -> None:
        self.existing = existing
        self.create_error = create_error
        self.created_name: str | None = None

    async def find_space_by_name(self, name: str) -> dict[str, object] | None:
        return self.existing

    async def create_space(self, name: str) -> dict[str, object]:
        if self.create_error:
            raise self.create_error
        self.created_name = name
        return {"id": "m-new", "name": name}


def test_ensure_space_returns_found_when_present() -> None:
    fake = _FakeClient(existing={"id": "m-1", "name": "research-agent-memory"})
    result = asyncio.run(
        ensure_space(
            "research-agent-memory",
            endpoint="https://app.datarobot.com",
            token="tok",
            client=fake,  # type: ignore[arg-type]
        )
    )
    assert result.success is True
    assert result.action == "found"
    assert result.space_id == "m-1"
    assert fake.created_name is None


def test_ensure_space_creates_when_absent() -> None:
    fake = _FakeClient(existing=None)
    result = asyncio.run(
        ensure_space(
            "research-agent-memory",
            endpoint="https://app.datarobot.com",
            token="tok",
            client=fake,  # type: ignore[arg-type]
        )
    )
    assert result.success is True
    assert result.action == "created"
    assert result.space_id == "m-new"
    assert fake.created_name == "research-agent-memory"


def test_ensure_space_surfaces_api_error() -> None:
    fake = _FakeClient(existing=None, create_error=MemoryApiError("forbidden"))
    result = asyncio.run(
        ensure_space(
            "research-agent-memory",
            endpoint="https://app.datarobot.com",
            token="tok",
            client=fake,  # type: ignore[arg-type]
        )
    )
    assert result.success is False
    assert result.error_message == "forbidden"
