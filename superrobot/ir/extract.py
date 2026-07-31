"""Assemble a Migration IR from the deterministic probes.

This is the seam between Layer 2 (probes producing facts with provenance)
and Layer 4 (the IR). It is deliberately dumb: it enumerates facts, decides
each one's disposition by rules that can be stated in a sentence, and
records both. No inference, no judgment -- that is Layer 3's job, and Layer
3 does not exist yet.

**What Phase 1 actually extracts: LLM call sites.** Nothing else. There is
no tool probe, no state probe, no orchestration probe, and entry-point
resolution is not wired in. That is a real hole in the coverage invariant:
the ledger can only reconcile facts a probe enumerated, so a repo full of
`@tool`-decorated functions produces a clean ledger while carrying none of
them over. Papering
over that would be exactly the silent-drop failure this architecture exists
to remove, so the extractor writes the hole into `residue` on every run.
Read `MigrationIR.residue` before trusting a clean ledger.

Disposition rules:

* known client, model resolved  -> MIGRATED
* known client, model unresolved -> BLOCKING (we would have to guess which
  model the agent uses, and guessing wrong is invisible at deploy time)
* unknown provider -> BLOCKING (no shim; migrating it means dropping a
  model call)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from superrobot.ir.ledger import CoverageLedger
from superrobot.ir.model import (
    Disposition,
    Evidence,
    LlmCall,
    MigrationIR,
    Residue,
    Severity,
    SourceFact,
)
from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.probes.llm_calls import LlmCallSite, find_llm_call_sites

_PHASE_ONE_GAP = (
    "entry points, tools, state, orchestration topology, external I/O and config were not extracted"
)


@dataclass(frozen=True)
class Extraction:
    """The IR and the ledger that accounts for it.

    They are returned together on purpose: an IR without its ledger is an
    assertion nobody checked.
    """

    ir: MigrationIR
    ledger: CoverageLedger

    def is_clean(self) -> bool:
        """The verdict that actually matters.

        `ledger.is_clean()` alone is not it. The ledger reconciles the facts
        a probe *enumerated*, so a repo where the probe found nothing
        reconciles perfectly -- three of our own fixtures do exactly that.
        Blocking residue is how "we found nothing and that is suspicious"
        gets represented, so it has to count here or the invariant has a
        hole shaped like a silent zero.
        """
        return self.ledger.is_clean() and not any(
            r.severity is Severity.BLOCKING for r in self.ir.residue
        )


def _fact_id(site: LlmCallSite, repo: Path) -> str:
    return f"llm_call:{_relative(site.site.file, repo)}:{site.site.line}:{site.client}"


def _relative(file: str, repo: Path) -> str:
    try:
        return str(Path(file).resolve().relative_to(repo.resolve()))
    except ValueError:
        return file


def _evidence(site: LlmCallSite) -> Evidence:
    return Evidence(file=site.site.file, line=site.site.line, node_id=site.site.node_id)


def _unresolved_model_expressions(site: LlmCallSite) -> list[str]:
    """The source expressions that were passed as a model but could not be
    resolved. Kept verbatim so the blocker can name what defeated us.
    """
    if site.model is not None:
        return []
    return [
        value
        for key, value in site.params.items()
        if key in ("model", "model_name", "model_id", "deployment_name", "azure_deployment")
    ]


def _disposition(site: LlmCallSite) -> tuple[Disposition, str | None]:
    if not site.known:
        return (
            Disposition.BLOCKING,
            f"no shim for provider {site.client!r}; migrating would drop this model call",
        )
    if site.model is None:
        expressions = _unresolved_model_expressions(site)
        detail = ", ".join(expressions) if expressions else "no model argument at the call site"
        return (
            Disposition.BLOCKING,
            f"{site.client} model could not be resolved statically ({detail}); "
            "guessing it would be invisible at deploy time",
        )
    return Disposition.MIGRATED, None


def extract_migration_ir(repo: Path) -> Extraction:
    """Build the IR and its coverage ledger for one source repo."""
    repo = Path(repo)
    graph = build_repo_graph(repo)
    sites = find_llm_call_sites(graph)

    facts = [
        SourceFact(
            id=_fact_id(site, repo),
            kind="llm_call",
            description=f"{site.client}(...)",
            file=site.site.file,
            line=site.site.line,
            node_id=site.site.node_id,
        )
        for site in sites
    ]
    ledger = CoverageLedger(facts)

    llm_calls: list[LlmCall] = []
    for site, fact in zip(sites, facts, strict=True):
        disposition, reason = _disposition(site)
        ledger.record(fact.id, disposition, reason=reason)
        llm_calls.append(
            LlmCall(
                client=site.client,
                model=site.model,
                unresolved_model=_unresolved_model_expressions(site),
                params=dict(site.params),
                known=site.known,
                evidence=[_evidence(site)],
                fact_id=fact.id,
            )
        )

    residue = [
        Residue(
            description=f"Phase 1 extraction: {_PHASE_ONE_GAP}",
            reason=(
                "no probe exists for them yet, so a clean ledger here does not "
                "mean the agent has none"
            ),
            severity=Severity.WARNING,
            evidence=[Evidence(file=str(repo), line=0)],
        )
    ]
    if not sites:
        residue.append(
            Residue(
                description="no LLM call site was found in this repo",
                reason=(
                    "far more likely that the probe missed a pattern than that an "
                    "agent talks to no model -- inspect before trusting this"
                ),
                severity=Severity.BLOCKING,
                evidence=[Evidence(file=str(repo), line=0)],
            )
        )

    ir = MigrationIR(
        source_repo=str(repo),
        name=repo.name,
        llm_calls=llm_calls,
        residue=residue,
        coverage=ledger.snapshot(),
    )
    return Extraction(ir=ir, ledger=ledger)
