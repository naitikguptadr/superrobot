"""CLI smoke tests for Spec 01 commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from superrobot.cli import _gap_gate, app
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


def test_deploy_workload_requires_image_uri(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["deploy", str(tmp_path), "--target", "workload", "--config-dir", str(tmp_path)]
    )
    assert result.exit_code == 2
    assert "--image-uri is required" in result.stdout


def test_deploy_workload_blocked_without_entitlement(tmp_path: Path) -> None:
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
            capabilities=CapabilityMatrix(llm_gateway=True, workload=False),
        ),
        tmp_path,
    )
    result = runner.invoke(
        app,
        [
            "deploy",
            str(tmp_path),
            "--target",
            "workload",
            "--image-uri",
            "registry.example.com/agent:1",
            "--config-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "not entitled" in result.stdout


def test_deploy_workload_rejects_malformed_secret_flag(tmp_path: Path) -> None:
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
            capabilities=CapabilityMatrix(llm_gateway=True, workload=True),
        ),
        tmp_path,
    )
    result = runner.invoke(
        app,
        [
            "deploy",
            str(tmp_path),
            "--target",
            "workload",
            "--image-uri",
            "registry.example.com/agent:1",
            "--secret",
            "NO_EQUALS_SIGN",
            "--config-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2
    assert "Invalid --secret" in result.stdout


_CLEAN_CUSTOM_PY = '_RUNTIME_PARAM_KEYS = ["API_KEY"]\n'
_CLEAN_INFRA_PY = "api_key_param = None  # references API_KEY\n"
_CLEAN_ENV_TEMPLATE = "API_KEY=\nPROMPT_TEMPLATE_ID=\nDATAROBOT_ENDPOINT=\n"


def _write_clean_package(root: Path) -> None:
    (root / "agent" / "agent").mkdir(parents=True)
    (root / "agent" / "agent" / "custom.py").write_text(_CLEAN_CUSTOM_PY)
    (root / "infra" / "infra").mkdir(parents=True)
    (root / "infra" / "infra" / "agent.py").write_text(_CLEAN_INFRA_PY)
    (root / ".env.template").write_text(_CLEAN_ENV_TEMPLATE)
    (root / "pyproject.toml").write_text('[project]\ndependencies = ["foo"]\n')


def test_validate_clean_package_passes(tmp_path: Path) -> None:
    _write_clean_package(tmp_path)
    result = runner.invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code == 0
    assert "no gaps found" in result.stdout


def test_validate_empty_dir_is_blocking(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code == 1
    assert "blocking" in result.stdout


def test_deploy_agent_app_blocked_by_gap_analysis(tmp_path: Path) -> None:
    # empty dir — no generated package files, so Gap Analysis blocks before any dr call
    result = runner.invoke(app, ["deploy", str(tmp_path), "--target", "agent-app"])
    assert result.exit_code == 1
    assert "Deploy refused" in result.stdout


def test_deploy_workload_blocked_by_gap_analysis_after_entitlement_checks(
    tmp_path: Path,
) -> None:
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
            capabilities=CapabilityMatrix(llm_gateway=True, workload=True),
        ),
        tmp_path,
    )
    result = runner.invoke(
        app,
        [
            "deploy",
            str(tmp_path),
            "--target",
            "workload",
            "--image-uri",
            "registry.example.com/agent:1",
            "--config-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "Deploy refused" in result.stdout


def test_gap_gate_blocks_without_waive_but_proceeds_with_waive(tmp_path: Path) -> None:
    # empty dir → blocking "not-a-package" finding
    assert _gap_gate(tmp_path, waive=False, json_out=False, target="agent-app") is False
    assert _gap_gate(tmp_path, waive=True, json_out=False, target="agent-app") is True


def test_memory_ensure_blocked_without_auth(tmp_path: Path) -> None:
    result = runner.invoke(app, ["memory", "ensure", "demo-space", "--config-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "Not authenticated" in result.stdout


def test_memory_ensure_blocked_without_entitlement(tmp_path: Path) -> None:
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
            capabilities=CapabilityMatrix(llm_gateway=True, memory=False),
        ),
        tmp_path,
    )
    result = runner.invoke(app, ["memory", "ensure", "demo-space", "--config-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "not entitled" in result.stdout


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
