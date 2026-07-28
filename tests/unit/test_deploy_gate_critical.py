"""Regression tests for the deploy gate and receipt handling (audit C4, C5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from superrobot import cli as cli_mod
from superrobot.cli import _resolve_source_repo, app
from superrobot.pipeline import deployer as deployer_mod
from superrobot.pipeline.deployer import DeployResult

runner = CliRunner()


def _generated_package(root: Path, *, deps: str = "[]") -> Path:
    """A package shaped the way run_gap_analysis expects."""
    pkg = root / ".superrobot"
    (pkg / "agent" / "agent").mkdir(parents=True)
    (pkg / "agent" / "agent" / "custom.py").write_text("_RUNTIME_PARAM_KEYS = []\n")
    (pkg / ".env.template").write_text("")
    (pkg / "pyproject.toml").write_text(f"[project]\ndependencies = {deps}\n")
    return pkg


def _source_repo(root: Path) -> None:
    """A source repo declaring deps the generated package drops."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\ndependencies = ["langgraph", "openai", "chromadb"]\n'
    )


class TestDeployGateSeesTheSameFindingsAsValidate:
    """C4 — _gap_gate called run_gap_analysis(path) with no source_repo, which
    structurally disabled the only blocking dependency rule. A package that
    `validate` refused would `deploy` clean, and the receipt recorded
    blocking: 0 -- the audit trail asserting a gate that never ran.
    """

    def test_source_repo_is_inferred_from_the_conventional_layout(self, tmp_path: Path) -> None:
        _source_repo(tmp_path)
        pkg = _generated_package(tmp_path)

        assert _resolve_source_repo(pkg, None) == tmp_path

    def test_an_explicit_source_wins_over_the_inference(self, tmp_path: Path) -> None:
        _source_repo(tmp_path)
        pkg = _generated_package(tmp_path)
        explicit = tmp_path / "elsewhere"
        explicit.mkdir()

        assert _resolve_source_repo(pkg, explicit) == explicit

    def test_no_inference_when_the_package_is_not_dot_superrobot(self, tmp_path: Path) -> None:
        pkg = tmp_path / "custom-out"
        pkg.mkdir()

        assert _resolve_source_repo(pkg, None) is None

    def test_deploy_is_blocked_by_the_dependency_rule_validate_would_catch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The end-to-end payoff: this package drops every declared
        dependency. `validate --source` always caught it; `deploy` did not.
        """
        _source_repo(tmp_path)
        pkg = _generated_package(tmp_path, deps="[]")

        deployed: list[str] = []

        async def _never(**kwargs: object) -> DeployResult:
            deployed.append("called")
            return DeployResult(success=True, stdout="", stderr="", warnings=[], error_message=None)

        monkeypatch.setattr(deployer_mod, "deploy", _never)

        result = runner.invoke(app, ["deploy", str(pkg), "--config-dir", str(tmp_path / "cfg")])

        assert result.exit_code == 1
        assert not deployed, "a package validate would block must not reach the platform"


class TestReceiptFailureDoesNotFailASuccessfulDeploy:
    """C5 — _record_receipt runs after the deploy has already landed. Any
    failure escaped uncaught: the platform was mutated, stdout said success,
    the process exited 1, and no receipt existed. CI then retried a deploy
    that had already happened.
    """

    def test_a_successful_deploy_still_exits_zero_when_the_receipt_cannot_be_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pkg = _generated_package(tmp_path)

        async def _ok(**kwargs: object) -> DeployResult:
            return DeployResult(success=True, stdout="", stderr="", warnings=[], error_message=None)

        def _boom(**kwargs: object) -> None:
            raise NotADirectoryError("receipts dir is a file")

        monkeypatch.setattr(deployer_mod, "deploy", _ok)
        monkeypatch.setattr(cli_mod, "_record_receipt", _boom)

        result = runner.invoke(app, ["deploy", str(pkg), "--config-dir", str(tmp_path / "cfg")])

        assert result.exit_code == 0, "the deploy landed; a bookkeeping failure must not mask that"

    def test_the_receipt_failure_is_still_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Losing the audit trail is worth telling the user about, even
        though it must not change the exit code.
        """
        pkg = _generated_package(tmp_path)

        async def _ok(**kwargs: object) -> DeployResult:
            return DeployResult(success=True, stdout="", stderr="", warnings=[], error_message=None)

        def _boom(**kwargs: object) -> None:
            raise NotADirectoryError("receipts dir is a file")

        monkeypatch.setattr(deployer_mod, "deploy", _ok)
        monkeypatch.setattr(cli_mod, "_record_receipt", _boom)

        result = runner.invoke(app, ["deploy", str(pkg), "--config-dir", str(tmp_path / "cfg")])

        assert "receipt" in result.output.lower()

    def test_a_failed_deploy_still_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pkg = _generated_package(tmp_path)

        async def _fail(**kwargs: object) -> DeployResult:
            return DeployResult(
                success=False, stdout="", stderr="", warnings=[], error_message="boom"
            )

        monkeypatch.setattr(deployer_mod, "deploy", _fail)

        result = runner.invoke(app, ["deploy", str(pkg), "--config-dir", str(tmp_path / "cfg")])

        assert result.exit_code == 1
