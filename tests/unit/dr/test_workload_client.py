"""Workload API client unit tests."""

from __future__ import annotations

import asyncio

import pytest

from superrobot.dr.workload_client import WorkloadApiError, WorkloadClient


def test_find_by_name_returns_match() -> None:
    async def transport(method: str, url: str, headers: dict[str, str], payload: object | None):
        assert method == "GET"
        assert "workloads" in url
        return 200, {"data": [{"id": "w-1", "name": "research-agent"}]}

    client = WorkloadClient("https://app.datarobot.com", "tok", transport=transport)
    found = asyncio.run(client.find_by_name("research-agent"))
    assert found == {"id": "w-1", "name": "research-agent"}


def test_find_by_name_returns_none_when_missing() -> None:
    async def transport(method: str, url: str, headers: dict[str, str], payload: object | None):
        return 200, {"data": []}

    client = WorkloadClient("https://app.datarobot.com", "tok", transport=transport)
    assert asyncio.run(client.find_by_name("ghost")) is None


def test_create_posts_manifest_and_returns_body() -> None:
    async def transport(method: str, url: str, headers: dict[str, str], payload: object | None):
        assert method == "POST"
        assert payload == {"name": "research-agent"}
        return 201, {"id": "w-2", "name": "research-agent"}

    client = WorkloadClient("https://app.datarobot.com", "tok", transport=transport)
    result = asyncio.run(client.create({"name": "research-agent"}))
    assert result["id"] == "w-2"


def test_replace_patches_by_id() -> None:
    async def transport(method: str, url: str, headers: dict[str, str], payload: object | None):
        assert method == "PATCH"
        assert url.endswith("/workloads/w-1/")
        return 200, {"id": "w-1", "name": "research-agent"}

    client = WorkloadClient("https://app.datarobot.com", "tok", transport=transport)
    result = asyncio.run(client.replace("w-1", {"name": "research-agent"}))
    assert result["id"] == "w-1"


def test_create_raises_on_non_2xx() -> None:
    async def transport(method: str, url: str, headers: dict[str, str], payload: object | None):
        return 403, {"detail": "forbidden"}

    client = WorkloadClient("https://app.datarobot.com", "tok", transport=transport)
    with pytest.raises(WorkloadApiError, match="forbidden"):
        asyncio.run(client.create({"name": "x"}))
