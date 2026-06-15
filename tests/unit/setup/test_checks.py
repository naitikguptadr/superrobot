"""Setup checks unit tests."""

from pathlib import Path

import pytest

from superrobot.setup import checks
from superrobot.setup.checks import (
    PrerequisiteStatus,
    SetupCheckResult,
    auth_matches_endpoint,
    check_prerequisites,
    dr_config_endpoint,
)


def test_check_prerequisites_returns_all_binaries() -> None:
    prereqs = check_prerequisites()
    assert len(prereqs) >= 6
    assert all(isinstance(p, PrerequisiteStatus) for p in prereqs)


def test_setup_check_result_not_ready_by_default() -> None:
    result = SetupCheckResult()
    assert not result.is_ready


def test_setup_check_result_ready_when_all_ok() -> None:
    result = SetupCheckResult(
        prerequisites=[PrerequisiteStatus("dr", True)],
        auth_ok=True,
        endpoint_set=True,
        token_set=True,
        gateway_ok=True,
    )
    assert result.is_ready


def test_dr_config_endpoint_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks, "DR_CONFIG_FILE", tmp_path / "drconfig.yaml")
    assert dr_config_endpoint() is None


def test_dr_config_endpoint_reads_and_normalizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "drconfig.yaml"
    cfg.write_text("endpoint: https://staging.datarobot.com/api/v2\ntoken: abc\n")
    monkeypatch.setattr(checks, "DR_CONFIG_FILE", cfg)
    assert dr_config_endpoint() == "https://staging.datarobot.com"


def test_dr_config_endpoint_malformed_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "drconfig.yaml"
    cfg.write_text("[: not yaml ::")
    monkeypatch.setattr(checks, "DR_CONFIG_FILE", cfg)
    assert dr_config_endpoint() is None


def test_auth_matches_endpoint_unknown_config_is_permissive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(checks, "DR_CONFIG_FILE", tmp_path / "missing.yaml")
    assert auth_matches_endpoint("https://staging.datarobot.com")


def test_auth_matches_endpoint_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "drconfig.yaml"
    cfg.write_text("endpoint: https://app.datarobot.com/api/v2\n")
    monkeypatch.setattr(checks, "DR_CONFIG_FILE", cfg)
    assert not auth_matches_endpoint("https://staging.datarobot.com")
    assert auth_matches_endpoint("https://app.datarobot.com/")
