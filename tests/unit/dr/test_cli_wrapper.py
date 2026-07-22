"""DrCliWrapper subprocess-failure handling."""

from __future__ import annotations

import asyncio

from superrobot.dr.cli_wrapper import DrCliWrapper


def test_missing_dr_binary_returns_result_instead_of_raising() -> None:
    wrapper = DrCliWrapper(dr_binary="dr-binary-that-does-not-exist")
    result = asyncio.run(wrapper.task_run_deploy(cwd="/tmp"))
    assert result.ok is False
    assert result.returncode == 127
    assert "command not found" in result.stderr


def test_missing_dr_binary_run_dev_returns_result_instead_of_raising() -> None:
    wrapper = DrCliWrapper(dr_binary="dr-binary-that-does-not-exist")
    result = asyncio.run(wrapper.run_dev("{}", cwd="/tmp"))
    assert result.ok is False
    assert result.returncode == 127
    assert "command not found" in result.stderr
