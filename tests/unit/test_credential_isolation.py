"""Regression tests for credential scoping (audit findings C6 / F1).

The suite used to load the developer's real `~/.config/superrobot/.env` into
process-global `os.environ` and spend the token against the live platform.
It also made `test_memory_ensure_blocked_without_auth` order-dependent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from superrobot.cli import _resolve_credentials, app

runner = CliRunner()


def test_explicit_config_dir_ignores_ambient_environment_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--config-dir <empty>` must mean "no credentials", even when the
    process environment happens to carry some. Previously the os.environ
    fallback fired regardless, so leaked credentials satisfied auth checks
    the caller had explicitly scoped away.
    """
    monkeypatch.setenv("DATAROBOT_ENDPOINT", "https://leaked.example.com")
    monkeypatch.setenv("DATAROBOT_API_TOKEN", "leaked-token")

    endpoint, token, state = _resolve_credentials(tmp_path)

    assert endpoint == ""
    assert token == ""
    assert state is None


def test_ambient_environment_is_still_used_when_no_config_dir_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unscoped path must keep working — this is how a normal CLI run
    picks up exported credentials.
    """
    monkeypatch.setenv("DATAROBOT_ENDPOINT", "https://ambient.example.com")
    monkeypatch.setenv("DATAROBOT_API_TOKEN", "ambient-token")

    endpoint, token, _ = _resolve_credentials(None)

    assert endpoint == "https://ambient.example.com"
    assert token == "ambient-token"


def test_memory_ensure_reports_not_authenticated_despite_leaked_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The order-dependent failure, pinned directly.

    Simulates the poisoned state a prior test used to leave behind and
    asserts the scoped command still reports the real problem rather than
    the misleading "not entitled".
    """
    monkeypatch.setenv("DATAROBOT_ENDPOINT", "https://leaked.example.com")
    monkeypatch.setenv("DATAROBOT_API_TOKEN", "leaked-token")

    result = runner.invoke(app, ["memory", "ensure", "demo-space", "--config-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "Not authenticated" in result.stdout


def test_conftest_isolates_the_real_config_directory() -> None:
    """The autouse fixture must actually redirect config away from $HOME,
    so no test can read or spend the developer's real token.
    """
    configured = os.environ.get("SUPERROBOT_CONFIG_DIR", "")

    assert configured, "SUPERROBOT_CONFIG_DIR should be set by the autouse fixture"
    assert Path.home() / ".config" / "superrobot" != Path(configured)
    assert not os.environ.get("DATAROBOT_API_TOKEN")
