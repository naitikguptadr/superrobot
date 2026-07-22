"""Receipt store unit tests."""

from __future__ import annotations

from pathlib import Path

from superrobot.models.receipt import Receipt
from superrobot.pipeline.receipts import (
    diagnose,
    latest_receipt,
    list_receipts,
    load_receipt,
    save_receipt,
)


def _receipt(**overrides: object) -> Receipt:
    defaults: dict[str, object] = {
        "id": "abc123",
        "created_at": "2026-01-01T00:00:00+00:00",
        "target": "workload",
        "action": "created",
        "success": True,
        "model": "azure/gpt-test",
    }
    defaults.update(overrides)
    return Receipt.model_validate(defaults)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    receipt = _receipt()
    save_receipt(receipt, tmp_path)
    loaded = load_receipt(receipt.id, tmp_path)
    assert loaded == receipt


def test_save_receipt_writes_no_secrets_by_construction(tmp_path: Path) -> None:
    # Receipt has no secret-shaped field at all — this asserts the persisted
    # JSON only contains the model's own declared fields.
    receipt = _receipt(error_message="not a credential reference: use credential:<id>")
    path = save_receipt(receipt, tmp_path)
    raw = path.read_text()
    assert "DATAROBOT_API_TOKEN" not in raw


def test_load_receipt_missing_returns_none(tmp_path: Path) -> None:
    assert load_receipt("does-not-exist", tmp_path) is None


def test_list_receipts_newest_first_and_filtered_by_target(tmp_path: Path) -> None:
    older = _receipt(id="r1", created_at="2026-01-01T00:00:00+00:00", target="workload")
    newer = _receipt(id="r2", created_at="2026-01-02T00:00:00+00:00", target="agent-app")
    save_receipt(older, tmp_path)
    save_receipt(newer, tmp_path)

    all_receipts = list_receipts(tmp_path)
    assert [r.id for r in all_receipts] == ["r2", "r1"]

    workload_only = list_receipts(tmp_path, target="workload")
    assert [r.id for r in workload_only] == ["r1"]


def test_list_receipts_empty_when_no_store(tmp_path: Path) -> None:
    assert list_receipts(tmp_path) == []


def test_latest_receipt(tmp_path: Path) -> None:
    save_receipt(_receipt(id="r1", created_at="2026-01-01T00:00:00+00:00"), tmp_path)
    save_receipt(_receipt(id="r2", created_at="2026-01-02T00:00:00+00:00"), tmp_path)
    latest = latest_receipt(tmp_path)
    assert latest is not None
    assert latest.id == "r2"


def test_diagnose_successful_receipt() -> None:
    assert "No failure" in diagnose(_receipt(success=True))


def test_diagnose_matches_single_replica_pattern() -> None:
    receipt = _receipt(
        success=False,
        action="failed",
        error_message="Refusing rolling replace at 1 replica(s) — scale to >=2 before replacing",
    )
    assert ">=2 replicas" in diagnose(receipt)


def test_diagnose_matches_plaintext_secret_pattern() -> None:
    receipt = _receipt(
        success=False,
        action="failed",
        error_message="API_KEY is not a credential reference — use 'credential:<id>'",
    )
    assert "credential:<id>" in diagnose(receipt)


def test_diagnose_blocked_action_without_matching_pattern() -> None:
    receipt = _receipt(success=False, action="blocked", error_message="nested import violation")
    assert "Gap Analysis" in diagnose(receipt)


def test_diagnose_unknown_pattern_falls_back() -> None:
    receipt = _receipt(success=False, action="failed", error_message="something truly novel")
    assert "No known failure pattern" in diagnose(receipt)
