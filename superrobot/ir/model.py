"""The Migration IR -- a semantic model of the source agent.

This is the frontend's output and the backend's only input. Everything
downstream (the `agent_spec.md` projection, the implementation writer, the
review surface a human reads) works from this, never from the source text.

Two invariants shape the schema:

**Every fact-bearing element carries evidence.** `IRElement.evidence` is
required and non-empty. An element with no `file:line` behind it is a claim
nobody can check, and unverifiable claims are how a migration silently
diverges from the agent it was supposed to reproduce. Making evidence
structurally mandatory means an LLM-produced claim cannot enter the IR
without something to reject it against.

**The IR is richer than `agent_spec.md` on purpose.** DataRobot's
`agent_spec.md` is a *greenfield design* artifact: it describes the agent
you want, so it has no representation for control-flow topology, state
backends, or prompt provenance. Projecting straight to it would discard
exactly the behavior that makes complex agents complex. So the Migration IR
holds the full understanding and `agent_spec.md` is a lossy projection of
it -- with the loss recorded in `residue` rather than left implicit.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Disposition(StrEnum):
    """What happened to a source fact. There is no fourth option, and no
    default -- see `superrobot.ir.ledger`.
    """

    MIGRATED = "migrated"
    DEFERRED = "deferred"
    BLOCKING = "blocking"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class TopologyKind(StrEnum):
    """How the agent's control flow is organized. `UNKNOWN` is honest and
    therefore allowed; it is not a synonym for `CUSTOM`.
    """

    SEQUENTIAL = "sequential"
    GRAPH = "graph"
    CREW = "crew"
    ROUTER = "router"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class Evidence(BaseModel):
    """Where a claim came from. `node_id` refers to a CPG node, so a claim
    can be re-checked against the graph rather than taken on trust.
    """

    file: str
    line: int
    node_id: str | None = None

    def __str__(self) -> str:
        return f"{self.file}:{self.line}"


class SourceFact(Evidence):
    """One thing the deterministic probes found in the source repo.

    Facts are what the coverage ledger reconciles. `kind` is deliberately a
    free-form string rather than an enum: a probe that discovers a category
    we have not enumerated yet must be able to report it. Rejecting the fact
    at the schema boundary would reintroduce the silent-drop failure mode
    this architecture exists to remove.

    Conventional kinds: `llm_call`, `tool`, `env_read`, `network_call`,
    `entry_point`, `state`, `orchestration`.
    """

    id: str
    kind: str
    description: str


class IRElement(BaseModel):
    """Base for everything in the IR that asserts something about the source.

    `evidence` is required and must be non-empty -- that is the whole point.
    """

    evidence: list[Evidence] = Field(min_length=1)
    fact_id: str | None = None
    """Links this element back to the `SourceFact` it accounts for, so the
    ledger can reconcile the two enumerations."""


class EntryPoint(IRElement):
    module: str
    function: str
    signature: str
    is_async: bool = False
    input_schema: dict[str, object] | None = None
    output_schema: dict[str, object] | None = None


class ToolParam(BaseModel):
    """One argument or return value of a tool. `object_schema` matches
    `agent_spec.md`'s own field name for structured types.
    """

    name: str
    type: str = "string"
    description: str | None = None
    object_schema: dict[str, object] | None = None


class ToolAuth(BaseModel):
    """Mirrors `agent_spec.md`'s `auth_spec`. `auth_method` is constrained to
    the set DataRobot's own reference table defines, so an unrepresentable
    scheme surfaces here rather than in a spec DataRobot's tooling rejects.
    """

    service_name: str
    auth_method: Literal[
        "api_key",
        "oauth2",
        "basic_auth",
        "bearer_token",
        "service_account",
        "other",
    ] = "other"


class Tool(IRElement):
    name: str
    callable: str
    """Fully-qualified name of the function implementing the tool."""
    description: str | None = None
    inputs: list[ToolParam] = Field(default_factory=list)
    outputs: list[ToolParam] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    auth: ToolAuth | None = None


class LlmCall(IRElement):
    """One place the agent talks to a model.

    `model` is `None` when the value could not be resolved statically; the
    expressions that defeated the dataflow probe are kept in
    `unresolved_model` rather than dropped, so the ledger can block on them.
    `known` is False for a provider we have no shim for -- surfaced, never
    skipped.
    """

    client: str
    provider: str | None = None
    model: str | None = None
    unresolved_model: list[str] = Field(default_factory=list)
    params: dict[str, str] = Field(default_factory=dict)
    prompt_provenance: list[str] = Field(default_factory=list)
    known: bool = True


class OrchestrationNode(BaseModel):
    name: str
    kind: str = "step"
    callable: str | None = None


class OrchestrationEdge(BaseModel):
    source: str
    target: str
    condition: str | None = None


class Orchestration(IRElement):
    kind: TopologyKind = TopologyKind.UNKNOWN
    nodes: list[OrchestrationNode] = Field(default_factory=list)
    edges: list[OrchestrationEdge] = Field(default_factory=list)


class StateItem(IRElement):
    name: str
    kind: str
    """`in_memory`, `persisted`, or `vector_store`."""
    backend: str | None = None
    scope: str | None = None


class ExternalIO(IRElement):
    kind: str
    """`http`, `db`, `filesystem`, or `vector`."""
    target: str | None = None
    direction: str = "unknown"
    callable: str | None = None


class ConfigVar(IRElement):
    name: str
    required: bool = True
    default: str | None = None
    consumers: list[str] = Field(default_factory=list)
    """Fully-qualified callables this value reaches, per the dataflow probe."""


class Residue(IRElement):
    """Something the source agent does that the migrated one will not.

    Recording residue is not an apology, it is the deliverable: an
    unrecorded behavioral difference is the only kind that hurts.
    """

    description: str
    reason: str
    severity: Severity = Severity.WARNING


class CoverageEntry(BaseModel):
    fact: SourceFact
    disposition: Disposition
    reason: str | None = None


class Coverage(BaseModel):
    """A serializable snapshot of the ledger, embedded in the IR so a
    reviewer sees the accounting alongside what it accounts for.
    """

    entries: list[CoverageEntry] = Field(default_factory=list)
    unaccounted: list[SourceFact] = Field(default_factory=list)

    def is_clean(self) -> bool:
        return not self.unaccounted and not any(
            e.disposition is Disposition.BLOCKING for e in self.entries
        )


class Frontend(BaseModel):
    """`agent_spec.md`'s `frontend` block. `chat` is the template's default
    UI; anything else means the recipe's frontend must be replaced.
    """

    type: Literal["chat", "multi-page", "custom"] = "chat"
    pages: list[str] = Field(default_factory=list)
    requirements: str | None = None


class MigrationIR(BaseModel):
    """The complete understanding of one source agent."""

    source_repo: str
    name: str
    description: str | None = None
    system_prompt: str | None = None
    examples: list[str] = Field(default_factory=list)
    """Representative user inputs. `agent_spec.md` requires this field, and
    `rehearsal.py` uses it to drive the pre-build simulation."""
    frontend: Frontend = Field(default_factory=Frontend)
    entry_points: list[EntryPoint] = Field(default_factory=list)
    tools: list[Tool] = Field(default_factory=list)
    llm_calls: list[LlmCall] = Field(default_factory=list)
    orchestration: Orchestration | None = None
    state: list[StateItem] = Field(default_factory=list)
    external_io: list[ExternalIO] = Field(default_factory=list)
    config: list[ConfigVar] = Field(default_factory=list)
    residue: list[Residue] = Field(default_factory=list)
    coverage: Coverage | None = None

    def primary_model(self) -> str | None:
        """The model to put in `agent_spec.md`. An agent using several is
        represented here by its first resolved one; the rest are the
        projection's problem to record as residue, not this method's to hide.
        """
        for call in self.llm_calls:
            if call.model:
                return call.model
        return None
