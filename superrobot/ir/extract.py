"""Assemble a Migration IR from the deterministic probes.

This is the seam between Layer 2 (probes producing facts with provenance)
and Layer 4 (the IR). It is deliberately dumb: it enumerates facts, decides
each one's disposition by rules that can be stated in a sentence, and
records both. No inference, no judgment -- that is Layer 3's job, and Layer
3 does not exist yet.

Five probes feed it: LLM call sites, tools, orchestration topology, entry
points, and config reads. Everything each probe *could not* read is carried
through as a blocking fact rather than dropped, so a clean ledger means
"we accounted for everything we saw", and blocking residue covers the
"we may not have seen everything" case.

Disposition rules, in one sentence each:

* LLM call: resolved provider and model -> MIGRATED. No provider, or a
  framework-default model with no explicit name, -> BLOCKING; both would
  otherwise mean the target silently talks to a different model.
* Tool: a named callable we could resolve -> MIGRATED. A `tools=` list we
  could see but not enumerate -> BLOCKING, because we know tools exist and
  cannot say which.
* Entry point: the highest-confidence candidate -> MIGRATED, the rest
  DEFERRED as alternatives. Choosing wrong produces an agent with the
  wrong interface, so the alternatives stay on the record.
* Config read: MIGRATED, or BLOCKING when the variable name itself could
  not be resolved.
* Orchestration: readable topology -> MIGRATED. A framework we recognized
  but could not read, and every individual `unresolved` note, -> BLOCKING.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from superrobot.ir.decisions import Decisions, FactDecision
from superrobot.ir.ledger import CoverageLedger
from superrobot.ir.model import (
    ConfigVar,
    Disposition,
    EntryPoint,
    Evidence,
    LlmCall,
    MigrationIR,
    Orchestration,
    OrchestrationEdge,
    OrchestrationNode,
    Residue,
    Severity,
    SourceFact,
    Tool,
    ToolParam,
    TopologyKind,
)
from superrobot.pipeline.graph.builder import RepoGraph, build_repo_graph
from superrobot.pipeline.graph.dataflow import Site
from superrobot.pipeline.probes.config import ConfigSite, find_config_sites
from superrobot.pipeline.probes.entry_points import EntryPointSite, find_entry_points
from superrobot.pipeline.probes.llm_calls import LlmCallSite, find_llm_call_sites
from superrobot.pipeline.probes.orchestration import OrchestrationFinding, find_orchestration
from superrobot.pipeline.probes.tools import ToolSite, find_tool_sites

# Which DataRobot recipe framework a detected topology maps onto. Anything
# we cannot place lands on `base`, which is the recipe's own escape hatch.
_FRAMEWORK_TO_RECIPE = {
    "langgraph": "langgraph",
    "crewai": "crewai",
    "llamaindex": "llamaindex",
    "llama_index": "llamaindex",
    "nat": "nat",
}

_CONFIDENCE_ORDER = {"console_script": 0, "main_guard": 1, "heuristic": 2}


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
        a probe *enumerated*, so a repo where the probes found nothing
        reconciles perfectly. Blocking residue is how "we found nothing and
        that is suspicious" gets represented, so it has to count here or the
        invariant has a hole shaped like a silent zero.
        """
        return self.ledger.is_clean() and not any(
            r.severity is Severity.BLOCKING for r in self.ir.residue
        )


def _evidence(site: Site) -> Evidence:
    return Evidence(file=site.file, line=site.line, node_id=site.node_id)


def _relative(file: str, repo: Path) -> str:
    try:
        return str(Path(file).resolve().relative_to(repo.resolve()))
    except ValueError:
        return file


class _Facts:
    """Accumulates source facts and their dispositions together, so a fact
    cannot be enumerated without one.
    """

    def __init__(self, repo: Path, decisions: Decisions) -> None:
        self._repo = repo
        self._decisions = decisions
        self.facts: list[SourceFact] = []
        self._pending: list[tuple[str, Disposition, str | None]] = []
        self.applied: set[str] = set()
        """Fact ids a human decision actually resolved, so a stale decisions
        file can be reported rather than silently under-applying."""

    def add(
        self,
        kind: str,
        label: str,
        site: Site,
        description: str,
        disposition: Disposition,
        reason: str | None = None,
    ) -> str:
        fact_id = f"{kind}:{_relative(site.file, self._repo)}:{site.line}:{label}"
        # Two findings can legitimately share a line (a decorator and the
        # function it wraps); disambiguate rather than lose one.
        unique = fact_id
        suffix = 2
        existing = {f.id for f in self.facts}
        while unique in existing:
            unique = f"{fact_id}#{suffix}"
            suffix += 1
        self.facts.append(
            SourceFact(
                id=unique,
                kind=kind,
                description=description,
                file=site.file,
                line=site.line,
                node_id=site.node_id,
            )
        )
        decision = self._decisions.for_fact(unique)
        if decision is not None:
            self.applied.add(unique)
            disposition, reason = decision.disposition, decision.ledger_reason()

        self._pending.append((unique, disposition, reason))
        return unique

    def decision_for(self, fact_id: str) -> FactDecision | None:
        return self._decisions.for_fact(fact_id)

    def into_ledger(self) -> CoverageLedger:
        ledger = CoverageLedger(self.facts)
        for fact_id, disposition, reason in self._pending:
            ledger.record(fact_id, disposition, reason=reason)
        return ledger


def _llm_disposition(site: LlmCallSite) -> tuple[Disposition, str | None]:
    if site.provider is None:
        return (
            Disposition.BLOCKING,
            f"could not resolve a provider for {site.client!r}; migrating it "
            "would mean guessing which service this call goes to",
        )
    if site.implicit_model:
        return (
            Disposition.BLOCKING,
            f"{site.client} relies on {site.provider}'s default model rather than "
            "naming one; the DataRobot recipe must be told a model explicitly, "
            "and inferring it here would be a guess",
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


def _unresolved_model_expressions(site: LlmCallSite) -> list[str]:
    """Source expressions passed as a model that we could not resolve. Kept
    verbatim so the blocker can name what defeated us.
    """
    if site.model is not None:
        return []
    return [
        value
        for key, value in site.params.items()
        if key in ("model", "model_name", "model_id", "deployment_name", "azure_deployment")
    ]


def _tool_param(arg_name: str, annotation: str | None) -> ToolParam:
    # `ToolParam.type` defaults to "str", so an unannotated argument would
    # silently acquire a type it never had. The caller records residue for
    # every one of these; the default here is only so the IR stays valid.
    return ToolParam(name=arg_name, type=annotation or "str")


def _collect_llm_calls(sites: list[LlmCallSite], facts: _Facts) -> list[LlmCall]:
    calls: list[LlmCall] = []
    for site in sites:
        disposition, reason = _llm_disposition(site)
        fact_id = facts.add(
            "llm_call",
            site.client,
            site.site,
            f"{site.client}(...)",
            disposition,
            reason,
        )
        # A decision may supply the model the probes could not resolve --
        # that is the whole point of deciding an implicit-default call.
        decision = facts.decision_for(fact_id)
        model = site.model or (decision.model if decision else None)

        calls.append(
            LlmCall(
                client=site.client,
                provider=site.provider,
                model=model,
                unresolved_model=_unresolved_model_expressions(site),
                params=dict(site.params),
                known=site.known,
                evidence=[_evidence(site.site)],
                fact_id=fact_id,
            )
        )
    return calls


def _collect_tools(sites: list[ToolSite], facts: _Facts, residue: list[Residue]) -> list[Tool]:
    tools: list[Tool] = []
    for site in sites:
        enumerable = not site.detection.startswith("tools_kwarg_unresolved")
        if enumerable:
            disposition, reason = Disposition.MIGRATED, None
        else:
            disposition, reason = (
                Disposition.BLOCKING,
                f"a tools list was passed at this call site ({site.detection}) but "
                "could not be enumerated statically; we know tools exist here and "
                "cannot say which",
            )
        fact_id = facts.add("tool", site.name, site.site, f"tool {site.name}", disposition, reason)

        for arg in site.inputs:
            if arg.type is None:
                residue.append(
                    Residue(
                        description=f"tool {site.name!r} parameter {arg.name!r} is unannotated",
                        reason=(
                            "the migrated tool schema will declare it as a string, "
                            "which may not be what the source accepted"
                        ),
                        severity=Severity.WARNING,
                        evidence=[_evidence(site.site)],
                    )
                )

        tools.append(
            Tool(
                name=site.name,
                callable=site.callable,
                description=site.description,
                inputs=[_tool_param(a.name, a.type) for a in site.inputs],
                outputs=[_tool_param(a.name, a.type) for a in site.outputs],
                evidence=[_evidence(site.site)],
                fact_id=fact_id,
            )
        )
    return tools


def _collect_entry_points(sites: list[EntryPointSite], facts: _Facts) -> list[EntryPoint]:
    ordered = sorted(sites, key=lambda s: _CONFIDENCE_ORDER.get(s.confidence, 99))
    entry_points: list[EntryPoint] = []
    for index, site in enumerate(ordered):
        if index == 0:
            disposition, reason = Disposition.MIGRATED, None
        else:
            disposition, reason = (
                Disposition.DEFERRED,
                f"alternative entry-point candidate ({site.confidence}); "
                f"{ordered[0].module}.{ordered[0].function} was selected instead",
            )
        fact_id = facts.add(
            "entry_point",
            f"{site.module}.{site.function}",
            site.site,
            f"{site.module}.{site.function}{site.signature}",
            disposition,
            reason,
        )
        entry_points.append(
            EntryPoint(
                module=site.module,
                function=site.function,
                signature=site.signature,
                is_async=site.is_async,
                input_schema={p.name: p.annotation or "unannotated" for p in site.parameters},
                output_schema={"returns": site.returns} if site.returns else None,
                evidence=[_evidence(site.site)],
                fact_id=fact_id,
            )
        )
    return entry_points


def _collect_config(sites: list[ConfigSite], facts: _Facts) -> list[ConfigVar]:
    config: list[ConfigVar] = []
    for site in sites:
        if site.unresolved_name:
            disposition, reason = (
                Disposition.BLOCKING,
                f"the environment variable name is computed at runtime ({site.name}); "
                "we cannot say which setting the migrated agent needs",
            )
        else:
            disposition, reason = Disposition.MIGRATED, None
        fact_id = facts.add(
            "env_read", site.name, site.site, f"reads {site.name}", disposition, reason
        )
        config.append(
            ConfigVar(
                name=site.name,
                required=site.required,
                default=site.default,
                consumers=list(site.consumers),
                evidence=[_evidence(site.site)],
                fact_id=fact_id,
            )
        )
    return config


def _collect_orchestration(
    finding: OrchestrationFinding | None, facts: _Facts
) -> Orchestration | None:
    if finding is None:
        return None

    if finding.kind is TopologyKind.UNKNOWN:
        disposition, reason = (
            Disposition.BLOCKING,
            f"an agent framework ({finding.framework}) is in use but no topology "
            "could be read from it; the migrated agent's control flow would be "
            "invented rather than carried over",
        )
    else:
        disposition, reason = Disposition.MIGRATED, None

    fact_id = facts.add(
        "orchestration",
        finding.framework or "unknown",
        finding.site,
        f"{finding.kind.value} topology ({len(finding.nodes)} node(s))",
        disposition,
        reason,
    )

    # Each thing the probe saw but could not read is its own blocking fact.
    # Folding them into one would let a long tail hide behind a single line.
    for index, note in enumerate(finding.unresolved):
        if _restates_the_unknown_topology(finding, note):
            # The UNKNOWN fact above already says exactly this. Two blockers
            # for one gap reads as two problems and makes the report noisier
            # without making it more complete.
            continue
        facts.add(
            "orchestration_gap",
            f"unresolved{index}",
            finding.site,
            note,
            Disposition.BLOCKING,
            "the orchestration probe saw this but could not read it",
        )

    return Orchestration(
        kind=finding.kind,
        nodes=[
            OrchestrationNode(name=n.name, kind=n.kind, callable=n.callable) for n in finding.nodes
        ],
        edges=[
            OrchestrationEdge(source=e.source, target=e.target, condition=e.condition)
            for e in finding.edges
        ],
        evidence=[_evidence(finding.site)],
        fact_id=fact_id,
    )


def _restates_the_unknown_topology(finding: OrchestrationFinding, note: str) -> bool:
    """True when an `unresolved` note says the same thing as an UNKNOWN
    topology fact already does, about the same framework.
    """
    return (
        finding.kind is TopologyKind.UNKNOWN
        and finding.framework is not None
        and finding.framework in note
        and "no topology was readable" in note
    )


def _target_framework(finding: OrchestrationFinding | None) -> str:
    if finding is None or finding.framework is None:
        return "base"
    return _FRAMEWORK_TO_RECIPE.get(finding.framework, "base")


def extract_migration_ir(
    repo: Path,
    *,
    graph: RepoGraph | None = None,
    decisions: Decisions | None = None,
) -> Extraction:
    """Build the IR and its coverage ledger for one source repo.

    `decisions` carries the human calls that resolve blockers. Without it
    every gap blocks, which is correct but terminal -- see
    `superrobot.ir.decisions` for why the resolution path is deliberately
    narrow.
    """
    repo = Path(repo)
    repo_graph = graph if graph is not None else build_repo_graph(repo)
    decisions = decisions or Decisions()

    llm_sites = find_llm_call_sites(repo_graph)
    tool_sites = find_tool_sites(repo_graph)
    entry_sites = find_entry_points(repo_graph)
    config_sites = find_config_sites(repo_graph)
    orchestration_finding = find_orchestration(repo_graph)

    facts = _Facts(repo, decisions)
    residue: list[Residue] = []

    llm_calls = _collect_llm_calls(llm_sites, facts)
    tools = _collect_tools(tool_sites, facts, residue)
    entry_points = _collect_entry_points(entry_sites, facts)
    config = _collect_config(config_sites, facts)
    orchestration = _collect_orchestration(orchestration_finding, facts)

    repo_evidence = [Evidence(file=str(repo), line=0)]
    if not llm_sites:
        residue.append(
            Residue(
                description="no LLM call site was found in this repo",
                reason=(
                    "far more likely that the probes missed a pattern than that an "
                    "agent talks to no model -- inspect before trusting this"
                ),
                severity=Severity.BLOCKING,
                evidence=repo_evidence,
            )
        )
    if not entry_sites:
        residue.append(
            Residue(
                description="no entry point could be resolved",
                reason=(
                    "the migrated agent would have no interface to expose; naming "
                    "one here would be a guess about how this agent is invoked"
                ),
                severity=Severity.BLOCKING,
                evidence=repo_evidence,
            )
        )
    residue.append(
        Residue(
            description=(
                "state backends, external I/O, prompt provenance and tool side "
                "effects were not extracted"
            ),
            reason="no probe exists for them yet; their absence here is not evidence of absence",
            severity=Severity.WARNING,
            evidence=repo_evidence,
        )
    )

    residue = _apply_residue_acknowledgements(residue, decisions)

    stale = sorted(set(decisions.facts) - facts.applied)
    if stale:
        # A decision naming a fact the probes did not produce means the file
        # has drifted from the code. Left silent, the run just blocks and
        # the human re-reads a decision they already made.
        residue.append(
            Residue(
                description=f"{len(stale)} decision(s) matched no source fact: " + ", ".join(stale),
                reason=(
                    "the decisions file has drifted from what the probes now find; "
                    "regenerate it rather than assuming those calls still apply"
                ),
                severity=Severity.BLOCKING,
                evidence=repo_evidence,
            )
        )

    ledger = facts.into_ledger()
    ir = MigrationIR(
        source_repo=str(repo),
        name=repo.name,
        system_prompt=decisions.system_prompt,
        examples=list(decisions.examples),
        frontend=decisions.frontend,
        target_framework=decisions.target_framework or _target_framework(orchestration_finding),
        entry_points=entry_points,
        tools=tools,
        llm_calls=llm_calls,
        orchestration=orchestration,
        config=config,
        residue=residue,
        coverage=ledger.snapshot(),
    )
    if decisions.model:
        # A top-level model answers every call site that named none, which
        # is the common case for framework-default agents.
        for call in ir.llm_calls:
            if call.model is None:
                call.model = decisions.model
    return Extraction(ir=ir, ledger=ledger)


def _apply_residue_acknowledgements(residue: list[Residue], decisions: Decisions) -> list[Residue]:
    """Downgrade acknowledged residue from blocking to a warning.

    Never removes it. The gap was real when we found it and is still real
    after someone accepted it -- what changes is whether it stops the
    migration, not whether it appears in the report.
    """
    applied: list[Residue] = []
    for entry in residue:
        if entry.severity is Severity.BLOCKING and decisions.acknowledges(entry.description):
            applied.append(
                entry.model_copy(
                    update={
                        "severity": Severity.WARNING,
                        "reason": f"{entry.reason} [acknowledged by a human decision]",
                    }
                )
            )
        else:
            applied.append(entry)
    return applied
