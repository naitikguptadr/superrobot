"""CLI smoke tests for Spec 01 commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from superrobot.cli import _gap_gate, app
from superrobot.setup.config import save_state, write_token_env
from superrobot.setup.models import AuthMethod, CapabilityMatrix, SetupState

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.4.0" in result.stdout


def test_doctor_json_not_ready(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["doctor", "--config-dir", str(tmp_path), "--json", "--skip-gateway"]
    )
    assert result.exit_code == 1
    assert "ready" in result.stdout


def test_deploy_workload_requires_image_uri_or_artifact_id(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["deploy", str(tmp_path), "--target", "workload", "--config-dir", str(tmp_path)]
    )
    assert result.exit_code == 2
    assert "Exactly one of --image-uri or --artifact-id" in result.stdout


def test_deploy_workload_rejects_both_image_uri_and_artifact_id(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "deploy",
            str(tmp_path),
            "--target",
            "workload",
            "--image-uri",
            "registry.example.com/agent:1",
            "--artifact-id",
            "artifact-abc123",
            "--config-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2
    assert "Exactly one of --image-uri or --artifact-id" in result.stdout


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
    result = runner.invoke(
        app, ["deploy", str(tmp_path), "--target", "agent-app", "--config-dir", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "Deploy refused" in result.stdout


def test_deploy_agent_app_json_output_is_pure_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: DEPLOY_WARNINGS used to print to stdout unconditionally,
    corrupting `--json` output with plain-text lines before the JSON payload."""
    from superrobot.dr.cli_wrapper import DrCommandResult

    class _FakeWrapper:
        async def task_run_deploy(self, cwd: str | None = None) -> DrCommandResult:
            return DrCommandResult(0, "deployed", "")

    monkeypatch.setattr("superrobot.pipeline.deployer.DrCliWrapper", _FakeWrapper)

    result = runner.invoke(
        app,
        [
            "deploy",
            str(tmp_path),
            "--target",
            "agent-app",
            "--waive",
            "--config-dir",
            str(tmp_path),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)  # raises if anything but JSON is on stdout
    assert payload["success"] is True
    assert payload["target"] == "agent-app"


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
    config_dir = tmp_path / "cfg"
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()
    assert (
        _gap_gate(
            package_dir, waive=False, json_out=False, target="agent-app", config_dir=config_dir
        )
        is None
    )
    report = _gap_gate(
        package_dir, waive=True, json_out=False, target="agent-app", config_dir=config_dir
    )
    assert report is not None
    assert report.blocking


def test_deploy_blocked_by_gap_analysis_writes_receipt(tmp_path: Path) -> None:
    runner.invoke(
        app, ["deploy", str(tmp_path), "--target", "agent-app", "--config-dir", str(tmp_path)]
    )
    result = runner.invoke(app, ["receipt", "operations", "--config-dir", str(tmp_path), "--json"])
    assert result.exit_code == 0
    assert '"target": "agent-app"' in result.stdout
    assert '"action": "blocked"' in result.stdout


def test_deploy_workload_blocked_receipt_records_artifact_id(tmp_path: Path) -> None:
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
    # empty dir -- no generated package files, so Gap Analysis blocks before any API call
    runner.invoke(
        app,
        [
            "deploy",
            str(tmp_path),
            "--target",
            "workload",
            "--artifact-id",
            "artifact-abc123",
            "--config-dir",
            str(tmp_path),
        ],
    )
    result = runner.invoke(app, ["receipt", "show", "--config-dir", str(tmp_path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["artifact_id"] == "artifact-abc123"
    assert payload["action"] == "blocked"


def test_receipt_show_defaults_to_latest(tmp_path: Path) -> None:
    runner.invoke(
        app, ["deploy", str(tmp_path), "--target", "agent-app", "--config-dir", str(tmp_path)]
    )
    result = runner.invoke(app, ["receipt", "show", "--config-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "agent-app/blocked" in result.stdout


def test_receipt_show_no_receipts_found(tmp_path: Path) -> None:
    result = runner.invoke(app, ["receipt", "show", "--config-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "No receipts found" in result.stdout


def test_receipt_diagnose_unknown_id(tmp_path: Path) -> None:
    result = runner.invoke(app, ["receipt", "diagnose", "ghost", "--config-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "No such receipt" in result.stdout


def test_receipt_diagnose_blocked_deploy(tmp_path: Path) -> None:
    runner.invoke(
        app, ["deploy", str(tmp_path), "--target", "agent-app", "--config-dir", str(tmp_path)]
    )
    latest = runner.invoke(app, ["receipt", "show", "--config-dir", str(tmp_path), "--json"])
    receipt_id = json.loads(latest.stdout)["id"]

    result = runner.invoke(app, ["receipt", "diagnose", receipt_id, "--config-dir", str(tmp_path)])
    assert result.exit_code == 0
    # empty dir → gap_analysis's "not-a-package" finding, a more specific match than
    # the generic "blocked" fallback
    assert "generated package" in result.stdout


def test_receipt_replace_reblocks_without_waive(tmp_path: Path) -> None:
    runner.invoke(
        app, ["deploy", str(tmp_path), "--target", "agent-app", "--config-dir", str(tmp_path)]
    )
    latest = runner.invoke(app, ["receipt", "show", "--config-dir", str(tmp_path), "--json"])
    receipt_id = json.loads(latest.stdout)["id"]

    result = runner.invoke(app, ["receipt", "replace", receipt_id, "--config-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "Deploy refused" in result.stdout

    # a second, distinct receipt should now exist, referencing the first
    ops = runner.invoke(app, ["receipt", "operations", "--config-dir", str(tmp_path), "--json"])
    receipts = json.loads(ops.stdout)
    assert len(receipts) == 2
    assert any(r.get("replaces") == receipt_id for r in receipts)


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
