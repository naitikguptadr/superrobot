"""Memory API client unit tests."""

from __future__ import annotations

import asyncio

import pytest

from superrobot.dr.memory_client import MemoryApiError, MemoryClient


def test_find_space_by_name_returns_match() -> None:
    async def transport(method: str, url: str, headers: dict[str, str], payload: object | None):
        assert method == "GET"
        assert "agenticMemory/spaces" in url
        return 200, {"data": [{"id": "m-1", "name": "research-agent-memory"}]}

    client = MemoryClient("https://app.datarobot.com", "tok", transport=transport)
    found = asyncio.run(client.find_space_by_name("research-agent-memory"))
    assert found == {"id": "m-1", "name": "research-agent-memory"}


def test_find_space_by_name_returns_none_when_missing() -> None:
    async def transport(method: str, url: str, headers: dict[str, str], payload: object | None):
        return 200, {"data": []}

    client = MemoryClient("https://app.datarobot.com", "tok", transport=transport)
    assert asyncio.run(client.find_space_by_name("ghost")) is None


def test_create_space_posts_name_and_returns_body() -> None:
    async def transport(method: str, url: str, headers: dict[str, str], payload: object | None):
        assert method == "POST"
        assert payload == {"name": "research-agent-memory"}
        return 201, {"id": "m-2", "name": "research-agent-memory"}

    client = MemoryClient("https://app.datarobot.com", "tok", transport=transport)
    result = asyncio.run(client.create_space("research-agent-memory"))
    assert result["id"] == "m-2"


def test_create_space_raises_on_non_2xx() -> None:
    async def transport(method: str, url: str, headers: dict[str, str], payload: object | None):
        return 403, {"detail": "forbidden"}

    client = MemoryClient("https://app.datarobot.com", "tok", transport=transport)
    with pytest.raises(MemoryApiError, match="forbidden"):
        asyncio.run(client.create_space("x"))
