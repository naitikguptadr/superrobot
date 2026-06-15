"""Headless import pipeline tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from superrobot.cli import _run_import_headless

FIXTURE = Path(__file__).parent.parent / "fixtures" / "langchain_agent"


@pytest.mark.asyncio
async def test_run_import_headless_writes_files(tmp_path: Path) -> None:
    out = tmp_path / "generated"
    await _run_import_headless(str(FIXTURE), str(out), skip_eval=True)
    assert (out / "agent/agent/custom.py").is_file()
    assert (out / "agent/agent/myagent.py").is_file()
    assert (out / "infra/infra/agent.py").is_file()


def test_run_import_headless_sync_entry(tmp_path: Path) -> None:
    out = tmp_path / "generated"
    asyncio.run(_run_import_headless(str(FIXTURE), str(out), skip_eval=True))
    assert (out / "pyproject.toml").is_file()
