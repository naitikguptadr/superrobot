"""Agent App deploy unit tests."""

from __future__ import annotations

import asyncio

from superrobot.dr.cli_wrapper import DrCommandResult
from superrobot.pipeline.deployer import deploy


class _FakeDr:
    def __init__(self, result: DrCommandResult) -> None:
        self.result = result
        self.calls = 0

    async def task_run_deploy(self, cwd: str | None = None) -> DrCommandResult:
        self.calls += 1
        return self.result


def test_deploy_success_surfaces_warnings() -> None:
    fake = _FakeDr(DrCommandResult(0, "ok", ""))
    result = asyncio.run(deploy(cwd="/tmp/out", has_ui=False, cli=fake))  # type: ignore[arg-type]
    assert result.success is True
    assert result.error_message is None
    assert result.warnings is not None
    assert any("15-20" in w for w in result.warnings)
    assert all("Frontend" not in w for w in result.warnings)
    assert fake.calls == 1


def test_deploy_failure_parses_stderr() -> None:
    fake = _FakeDr(DrCommandResult(1, "", "pulumi error: stack failed"))
    result = asyncio.run(deploy(cwd="/tmp/out", has_ui=True, cli=fake))  # type: ignore[arg-type]
    assert result.success is False
    assert result.error_message == "pulumi error: stack failed"
    assert result.warnings is not None
    assert any("Frontend" in w for w in result.warnings)
