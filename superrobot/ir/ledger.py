"""The coverage ledger -- the governing invariant of the whole architecture.

The deterministic probes enumerate what the source agent *does*. The IR
enumerates what the migration *carried over*. The ledger reconciles the two
and refuses to let the difference go unnamed.

Every source fact must end up in exactly one of three states:

* `MIGRATED` -- present in the IR and in the generated implementation
* `DEFERRED` -- deliberately left behind, with a reason on the record
* `BLOCKING` -- cannot be represented; the migration stops here

There is no fourth state and, critically, **no default**. A fact nobody
recorded is `unaccounted()`, `is_clean()` is False, and nothing downstream
runs. That is the structural answer to the failure this project was built
around: a fourteen-name regex that missed aliased imports, module-qualified
calls, and unknown providers *silently*. We will still miss exotic patterns.
We will no longer miss them quietly.

Two rules follow from that and are enforced here rather than by convention:

* A `DEFERRED` or `BLOCKING` record without a reason is rejected. An
  unexplained deferral is indistinguishable from an oversight, which makes
  the ledger a rubber stamp.
* Re-recording a fact with a different disposition is rejected. Silent
  overwrite would let a late `MIGRATED` erase an earlier `BLOCKING`.
"""

from __future__ import annotations

from collections.abc import Iterable

from superrobot.ir.model import Coverage, CoverageEntry, Disposition, SourceFact

_NEEDS_REASON = (Disposition.DEFERRED, Disposition.BLOCKING)


class LedgerError(Exception):
    """The ledger was asked to do something that would let a fact escape
    accounting.
    """


class CoverageLedger:
    """Reconciles enumerated source facts against their dispositions.

    Facts keep their insertion order throughout, so reports read in the
    order the probes walked the repo rather than in dictionary order.
    """

    def __init__(self, facts: Iterable[SourceFact]) -> None:
        self._facts: dict[str, SourceFact] = {}
        for fact in facts:
            if fact.id in self._facts:
                raise LedgerError(f"duplicate source fact id {fact.id!r}: one would mask the other")
            self._facts[fact.id] = fact
        self._entries: dict[str, CoverageEntry] = {}

    def record(
        self,
        fact_id: str,
        disposition: Disposition,
        reason: str | None = None,
    ) -> None:
        fact = self._facts.get(fact_id)
        if fact is None:
            raise LedgerError(
                f"cannot record unknown source fact {fact_id!r}; "
                "the ledger only accounts for facts the probes enumerated"
            )
        if disposition in _NEEDS_REASON and not (reason and reason.strip()):
            raise LedgerError(
                f"{disposition.value} requires a reason (fact {fact_id!r}); "
                "an unexplained disposition is indistinguishable from an oversight"
            )

        existing = self._entries.get(fact_id)
        if existing is not None and existing.disposition is not disposition:
            raise LedgerError(
                f"fact {fact_id!r} is already recorded as {existing.disposition.value}; "
                f"refusing to overwrite it with {disposition.value}"
            )

        self._entries[fact_id] = CoverageEntry(fact=fact, disposition=disposition, reason=reason)

    def unaccounted(self) -> list[SourceFact]:
        """Facts nobody dispositioned. Never empty by accident -- the only
        way to empty it is to record every fact.
        """
        return [fact for fact_id, fact in self._facts.items() if fact_id not in self._entries]

    def blocking(self) -> list[SourceFact]:
        return [
            entry.fact
            for entry in self._entries.values()
            if entry.disposition is Disposition.BLOCKING
        ]

    def deferred(self) -> list[CoverageEntry]:
        return [e for e in self._entries.values() if e.disposition is Disposition.DEFERRED]

    def migrated(self) -> list[SourceFact]:
        return [
            entry.fact
            for entry in self._entries.values()
            if entry.disposition is Disposition.MIGRATED
        ]

    def is_clean(self) -> bool:
        """True only when every fact is accounted for and none blocks.

        Deferrals do not dirty the ledger -- they are a recorded decision,
        not a gap.
        """
        return not self.unaccounted() and not self.blocking()

    def snapshot(self) -> Coverage:
        """The serializable form embedded in `MigrationIR.coverage`."""
        return Coverage(entries=list(self._entries.values()), unaccounted=self.unaccounted())

    def report(self) -> str:
        """Human-readable accounting. Names every gap, with its reason and
        provenance, so the report itself is the escalation.
        """
        lines = [
            f"Coverage: {len(self._facts)} source fact(s), {len(self._entries)} accounted for."
        ]

        blocking = [e for e in self._entries.values() if e.disposition is Disposition.BLOCKING]
        if blocking:
            lines.append("")
            lines.append("BLOCKING -- migration cannot proceed:")
            lines.extend(
                f"  {e.fact.id} ({e.fact.kind}) at {e.fact.file}:{e.fact.line}"
                f" -- {e.fact.description}: {e.reason}"
                for e in blocking
            )

        unaccounted = self.unaccounted()
        if unaccounted:
            lines.append("")
            lines.append("UNACCOUNTED -- found in the source, never dispositioned:")
            lines.extend(
                f"  {f.id} ({f.kind}) at {f.file}:{f.line} -- {f.description}" for f in unaccounted
            )

        deferred = self.deferred()
        if deferred:
            lines.append("")
            lines.append("DEFERRED -- deliberately not carried over:")
            lines.extend(
                f"  {e.fact.id} ({e.fact.kind}) at {e.fact.file}:{e.fact.line}: {e.reason}"
                for e in deferred
            )

        if self.is_clean():
            lines.append("")
            lines.append("No gaps: every source fact is migrated or explicitly deferred.")

        return "\n".join(lines)
