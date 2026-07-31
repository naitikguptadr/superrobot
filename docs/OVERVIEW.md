# SuperRobot — Overview and Plan

**What it is:** a tool that takes an existing Python AI agent — LangGraph,
CrewAI, AutoGen, LlamaIndex, Haystack, Semantic Kernel, smolagents — and
migrates it to run on DataRobot.

**The property that matters:** it cannot silently lose behavior. Every
fact it finds in the source is either migrated, explicitly deferred with a
reason, or it blocks the migration and says why.

---

## The problem

Customers have working agents. Getting them onto DataRobot is manual,
slow, and expert-dependent — a real adoption blocker. The obvious move is
to automate the port. The non-obvious part is that generating DataRobot
code is the easy half; *understanding what the source agent actually
does* is the hard half, and it's where naive approaches fail invisibly.

## Why the first approach failed

The original pipeline pattern-matched source text: a regex over 14 known
LLM client constructor names. Measured against realistic code, it missed
aliased imports, module-qualified calls, config-assigned classes, and
unknown providers — and it rewrote a class name inside a string literal.
**0 of 5 correct, with a false positive.** Every failure was silent.

It also targeted a superseded DataRobot contract (all 15 Jinja templates
emitted the wrong shape), and hand-rolled a `.datarobot/` scaffold that
DataRobot's own scripts generate — the missing directory that broke our
first real deploy.

Common root cause: the tool tried to *be* the compiler backend for a
target it doesn't own.

## The approach

Treat it as a compiler, not a rewriter. Four decisions follow:

1. **Recompile, don't rewrite.** Extract a semantic model; generate
   against the target from that model.
2. **Own only the frontend.** Extraction is the novel, defensible part.
   The backend is DataRobot's — use their recipe and their scripts.
3. **Deterministic code for transformation, LLM only for judgment**, with
   every LLM claim carrying checkable provenance.
4. **Nothing is silently dropped.** The governing invariant.

## Architecture

```
Pi harness  --  the agent orchestrates, consulting DataRobot skills
     |
     +-- our tools (deterministic)        DataRobot scripts (authoritative)
     |     sr_extract    CPG -> IR          clone_template.py
     |     sr_report     coverage ledger    select_framework.py
     |     sr_decisions  unblock a gap      setup_template.py
     |     sr_spec       IR -> agent_spec   list_llm_models.py
     |     sr_scaffold   run DR's scripts   rehearsal.py
     |                                      dr dependency check
     +-- sr_implement    (Phase 2) LLM writes impl, verifier-gated
```

**Layer 1 — Code property graph.** AST plus jedi call edges plus
intraprocedural dataflow. Dataflow is the highest-leverage piece: it
answers `CLS = ChatOpenAI; llm = CLS(model=cfg)` — which model actually
reaches that call — deterministically. Regex provably cannot.

**Layer 2 — Semantic probes.** Deterministic queries returning facts with
`file:line` provenance. Six of them: LLM call sites, providers, tools,
orchestration topology, entry points, config reads. Probes never guess; an
unresolvable value is reported as unresolved, and that becomes the thing
that gets escalated.

**Layer 3 — LLM interpretation (Phase 2).** Input is evidence bundles
from the probes, not raw source. Output is IR claims that must cite graph
nodes. A claim whose citation doesn't check out is rejected, not trusted.

**Layer 4 — Two-layer IR.**
- *Migration IR* (ours, rich): entry points, tools, LLM calls,
  orchestration, state, external I/O, config, residue, coverage. Every
  element carries evidence — structurally required, not by convention.
- *`agent_spec.md`* (DataRobot's, interchange): the lossy projection their
  toolchain consumes.

The Migration IR exists because `agent_spec.md` is a *greenfield design*
artifact with no representation for control-flow topology, state, or
prompt provenance. Projecting straight to it would discard exactly what
makes complex agents complex. What doesn't fit rides along as comments on
the emitted artifact.

**The IR is the review surface.** A 200-file agent collapses to a few
hundred lines of typed, diffable spec. You review the tool's
*understanding*, not a 4,000-line output diff.

**Layer 5 — Scaffold from DataRobot.** `clone_template.py` (pinned to tag
11.10.7) then `select_framework.py` then `setup_template.py`. Framework
choice comes from the detected topology, not an import-name guess. This
fixes the `.datarobot/` gap by construction and makes upstream drift a
version bump instead of a rewrite.

**Layer 6 — Implementation (Phase 2).** The harness agent writes the
implementation into the cloned recipe. Trust comes from the verifier, not
the model: parse and import checks, `dr dependency check`, `rehearsal.py`
simulation, ledger reconciliation, bounded repair, then escalate.

## The coverage ledger — the governing invariant

Probes enumerate **source facts**. The IR enumerates **migrated facts**.
The ledger reconciles them. Every source fact must be exactly one of:

- **migrated** — carried over
- **deferred** — deliberately not, with a reason on the record
- **blocking** — cannot be represented; migration stops

There is no fourth state and **no default**, so a fact nobody accounted
for lands in `unaccounted()` and nothing downstream runs. We will still
miss exotic patterns. We will no longer miss them quietly.

Because a ledger over *zero* facts reconciles perfectly, "we found
nothing suspicious-looking" is carried separately as blocking **residue** —
finding no LLM call in an agent repo is a blocker, not a pass.

### Resolving a blocker

A tool that can only ever block is not a tool. A **decisions file** lets a
human answer blockers one fact at a time. Four rules keep it from becoming
a rubber stamp:

- A decision cannot name a fact the probes don't produce — a drifted file
  blocks loudly rather than silently under-applying.
- A decision cannot *create* a block, only resolve one.
- Every decision needs a reason, including `migrated`. It's written into
  the ledger prefixed as a human decision, so reports always distinguish
  what was derived from what someone asserted.
- Acknowledging residue downgrades it to a warning that still appears —
  never deletes it.

The generated template is entirely commented out, so an unattended run
cannot accidentally acknowledge anything.

## Current status

Measured on nine bundled fixtures. Verified, not asserted.

**Working**
- Entry points with full signatures, all candidates ranked by confidence
- Tools via decorators, constructors, and `tools=[...]` arguments
- LangGraph node/edge topology; CrewAI agents and tasks; LlamaIndex and
  LCEL chains
- Env vars plus, via dataflow, which callables each one reaches
- Model resolution through aliases, local variables, and config dicts
- Provider resolution across langchain, openai, anthropic, autogen,
  haystack, semantic-kernel, smolagents, crewai, llamaindex
- Coverage ledger, decisions mechanism, `agent_spec.md` projection
- Pi harness tools; DataRobot scaffold wrappers

**Blocks, correctly**
- Framework-default models (CrewAI, LlamaIndex, smolagents name no model)
- Providers that can't be identified
- Conditional graph edges whose targets are chosen at runtime
- Env vars whose name is computed

**Not built**
- No probe for state backends, external I/O, prompt provenance, or tool
  side effects. The IR records this in `residue` on every run rather than
  letting their absence read as absence in the agent.
- No LLM interpretation layer, no implementation writer. The pipeline
  takes you to a reviewed spec and a scaffolded recipe, not a finished
  agent.
- `sr_scaffold` is tested against a fake runner only. It has not been run
  against live DataRobot, so the `.datarobot/` claim rests on reading
  their script, not on a deploy.

Gates: 496 Python tests, 66 shell tests, ruff and mypy and tsc clean.

## Roadmap

**Phase 1 (done).** CPG with dataflow, six probes, Migration IR, coverage
ledger, decisions mechanism, `agent_spec.md` projection, scaffold
wrappers, Pi harness interface.

**Phase 2.** LLM interpretation with provenance validation. Implementation
writer plus verify/repair loop. `rehearsal.py` and `dr dependency check`
wired into verification. Probes for state, external I/O, and side effects.
Deploy lifecycle observation.

**Phase 3.** Delete the legacy CLI, templates, `config_generator`, and
`ast_migrate`. Differential equivalence: record the original agent's
behavior against a mocked LLM layer, replay through the migrated one, diff
the traces — the only thing that actually *proves* a migration preserved
behavior. OTel monitoring onboarding.

## Risks

- **The recipe is a moving target.** Mitigated by pinning a template
  version and consuming DataRobot's scripts rather than duplicating their
  contract. A test asserts our framework list still matches theirs, so
  drift fails in CI rather than at deploy.
- **Dataflow analysis is genuinely hard.** Intraprocedural first, which
  covers the observed failure cases; interprocedural only if evidence
  demands it.
- **LLM-written implementation can be wrong.** It is never trusted — the
  verifier gates it and the ledger bounds what can silently escape.
- **Probes have a long tail.** Accepted and made visible: every probe
  records what it could not read, so the gap is in the report rather than
  in the output.

## Further reading

- [TRYING-IT.md](TRYING-IT.md) — runnable walkthrough, real outputs
- [Architecture spec](superpowers/specs/2026-07-28-ir-based-migration-architecture.md)
- [Phase 1 plan](superpowers/plans/2026-07-28-ir-migration-phase1.md)
