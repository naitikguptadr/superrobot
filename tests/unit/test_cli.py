"""CLI smoke tests for Spec 01 commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from superrobot.cli import app
from superrobot.setup.config import save_state, write_token_env
from superrobot.setup.models import AuthMethod, CapabilityMatrix, SetupState

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.3.0" in result.stdout


def test_doctor_json_not_ready(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["doctor", "--config-dir", str(tmp_path), "--json", "--skip-gateway"]
    )
    assert result.exit_code == 1
    assert "ready" in result.stdout


def test_status_ready_with_config(tmp_path: Path) -> None:
    write_token_env(
        endpoint="https://app.datarobot.com/api/v2",
        token="tok",
        model="azure/gpt-test",
        root=tmp_path,
    )
    save_state(
        SetupState(
            endpoint="https://app.datarobot.com",
            auth_method=AuthMethod.API_TOKEN,
            capabilities=CapabilityMatrix(llm_gateway=True),
        ),
        tmp_path,
    )
    result = runner.invoke(app, ["status", "--config-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "ready" in result.stdout.lower()
