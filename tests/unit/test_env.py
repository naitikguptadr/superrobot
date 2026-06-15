"""User env file tests."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from superrobot import env as env_module
from superrobot.env import read_env_file, write_env_file


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / "superrobot"
    target = config_dir / ".env"
    monkeypatch.setattr(env_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(env_module, "ENV_FILE", target)
    return target


def test_write_env_file_round_trip(env_file: Path) -> None:
    write_env_file({"DATAROBOT_ENDPOINT": "https://staging.datarobot.com", "A": "1"})
    values = read_env_file()
    assert values["DATAROBOT_ENDPOINT"] == "https://staging.datarobot.com"
    assert values["A"] == "1"


def test_write_env_file_merges_existing_keys(env_file: Path) -> None:
    write_env_file({"KEEP_ME": "yes", "DATAROBOT_API_TOKEN": "old"})
    write_env_file({"DATAROBOT_API_TOKEN": "new"})
    values = read_env_file()
    assert values["KEEP_ME"] == "yes"
    assert values["DATAROBOT_API_TOKEN"] == "new"


def test_write_env_file_is_owner_only(env_file: Path) -> None:
    write_env_file({"DATAROBOT_API_TOKEN": "secret"})
    mode = stat.S_IMODE(env_file.stat().st_mode)
    assert mode == 0o600
