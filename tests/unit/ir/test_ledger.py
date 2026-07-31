"""The coverage ledger -- no source fact may go unaccounted for."""

from __future__ import annotations

import pytest

from superrobot.ir.ledger import CoverageLedger, LedgerError
from superrobot.ir.model import Disposition, SourceFact


def _fact(fact_id: str) -> SourceFact:
    return SourceFact(id=fact_id, kind="llm_call", description=fact_id, file="main.py", line=1)


def test_a_fully_accounted_ledger_is_clean() -> None:
    ledger = CoverageLedger([_fact("a"), _fact("b")])
    ledger.record("a", Disposition.MIGRATED)
    ledger.record("b", Disposition.DEFERRED, reason="user opted out")

    assert ledger.is_clean()
    assert ledger.blocking() == []


def test_an_unaccounted_fact_blocks() -> None:
    """The governing invariant: silence is impossible."""
    ledger = CoverageLedger([_fact("a"), _fact("b")])
    ledger.record("a", Disposition.MIGRATED)

    assert not ledger.is_clean()
    assert [f.id for f in ledger.unaccounted()] == ["b"]


def test_a_blocking_disposition_blocks() -> None:
    ledger = CoverageLedger([_fact("a")])
    ledger.record("a", Disposition.BLOCKING, reason="no shim for this provider")

    assert not ledger.is_clean()
    assert [f.id for f in ledger.blocking()] == ["a"]


def test_deferring_without_a_reason_is_rejected() -> None:
    """A deferral with no explanation is indistinguishable from silence."""
    ledger = CoverageLedger([_fact("a")])

    with pytest.raises(LedgerError):
        ledger.record("a", Disposition.DEFERRED)


def test_recording_an_unknown_fact_is_rejected() -> None:
    ledger = CoverageLedger([_fact("a")])

    with pytest.raises(LedgerError):
        ledger.record("nonexistent", Disposition.MIGRATED)


def test_two_facts_sharing_an_id_are_rejected() -> None:
    """One would mask the other, and the masked one would never appear in
    `unaccounted()` -- silence through collision.
    """
    with pytest.raises(LedgerError):
        CoverageLedger([_fact("a"), _fact("a")])


def test_a_disposition_cannot_be_silently_overwritten() -> None:
    """Otherwise a later MIGRATED erases an earlier BLOCKING and the
    migration proceeds on a gap someone already found.
    """
    ledger = CoverageLedger([_fact("a")])
    ledger.record("a", Disposition.BLOCKING, reason="no shim")

    with pytest.raises(LedgerError):
        ledger.record("a", Disposition.MIGRATED)

    assert [f.id for f in ledger.blocking()] == ["a"]


def test_the_snapshot_carries_the_same_verdict_as_the_ledger() -> None:
    ledger = CoverageLedger([_fact("a"), _fact("b")])
    ledger.record("a", Disposition.MIGRATED)

    snapshot = ledger.snapshot()

    assert not snapshot.is_clean()
    assert [f.id for f in snapshot.unaccounted] == ["b"]


def test_the_report_names_every_gap() -> None:
    ledger = CoverageLedger([_fact("a"), _fact("b")])
    ledger.record("a", Disposition.BLOCKING, reason="unsupported provider")

    report = ledger.report()

    assert "unsupported provider" in report
    assert "b" in report, "an unaccounted fact must appear in the report"
