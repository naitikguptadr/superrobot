"""Setup runner unit tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from superrobot.setup import runner as runner_module
from superrobot.setup.runner import SetupRunner


class _FakeProc:
    returncode = 0

    async def wait(self) -> int:
        return 0


@pytest.fixture
def captured_exec(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    async def fake_exec(*args: str, **_kwargs: Any) -> _FakeProc:
        calls.append(args)
        return _FakeProc()

    monkeypatch.setattr(runner_module.asyncio, "create_subprocess_exec", fake_exec)
    return calls


async def test_auth_login_targets_staging_endpoint(
    captured_exec: list[tuple[str, ...]],
) -> None:
    runner = SetupRunner()
    assert runner.cli is not None
    runner.cli.auth_check = AsyncMock(return_value=True)  # type: ignore[method-assign]

    ok = await runner._run_auth_login("https://staging.datarobot.com/")
    assert ok
    assert captured_exec == [("dr", "auth", "login", "https://staging.datarobot.com")]


async def test_auth_login_without_endpoint_omits_url(
    captured_exec: list[tuple[str, ...]],
) -> None:
    runner = SetupRunner()
    assert runner.cli is not None
    runner.cli.auth_check = AsyncMock(return_value=True)  # type: ignore[method-assign]

    ok = await runner._run_auth_login()
    assert ok
    assert captured_exec == [("dr", "auth", "login")]
