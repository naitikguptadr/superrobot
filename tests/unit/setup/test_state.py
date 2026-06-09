"""Setup state unit tests."""

from pathlib import Path

import pytest

from superrobot.setup import state as state_module
from superrobot.setup.state import SetupState, mark_setup_complete


def test_setup_state_incomplete_by_default() -> None:
    state = SetupState()
    assert not state.is_complete


def test_setup_state_complete_when_all_flags_set() -> None:
    state = SetupState(
        completed_at="2026-01-01T00:00:00+00:00",
        datarobot_endpoint="https://app.datarobot.com",
        prerequisites_ok=True,
        auth_ok=True,
        gateway_ok=True,
    )
    assert state.is_complete


def test_mark_setup_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state_module, "STATE_FILE", tmp_path / "setup.yaml")
    state = mark_setup_complete("https://app.datarobot.com")
    assert state.is_complete
    assert state.datarobot_endpoint == "https://app.datarobot.com"
    assert (tmp_path / "setup.yaml").exists()
