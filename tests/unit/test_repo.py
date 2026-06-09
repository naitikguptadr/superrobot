"""Repository clone unit tests."""

from pathlib import Path

import pytest

from superrobot.repo import parse_github_url


def test_parse_github_https_url() -> None:
    url = parse_github_url("https://github.com/user/my-agent")
    assert url == "https://github.com/user/my-agent.git"


def test_parse_github_ssh_url() -> None:
    url = parse_github_url("git@github.com:user/my-agent.git")
    assert url == "https://github.com/user/my-agent.git"


def test_parse_github_invalid() -> None:
    assert parse_github_url("not-a-url") is None


@pytest.mark.asyncio
async def test_clone_local_path_returns_resolved(tmp_path: Path) -> None:

    from superrobot.repo import clone_repository

    local = tmp_path / "agent"
    local.mkdir()
    result = await clone_repository(str(local))
    assert result == local.resolve()
