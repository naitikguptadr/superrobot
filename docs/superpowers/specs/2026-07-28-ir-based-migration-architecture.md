# IR-Based Brownfield Migration — Architecture Design

**Status:** approved design, pre-implementation. Supersedes the pattern-matching pipeline.

**Goal:** Point the tool at an arbitrary Python agent repo — including large, complex, multi-framework ones — and produce a working DataRobot-native agent, with every behavioral gap surfaced rather than silently dropped.

---

## Why the current architecture cannot get there

Three structural problems, each verified against the code:

**It transforms text, not behavior.** LLM-client rewiring is a regex over 14 hardcoded constructor names. Measured coverage on realistic patterns: aliased imports missed, module-qualified calls missed (the regex's `(?<![\w.])` explicitly refuses them), config-assigned classes missed, unknown providers missed, raw SDK calls missed — and it rewrites the name inside string literals. Every miss is silent.

**It targets a superseded contract.** `af-component-agent` emits no `custom.py`; raw DRUM is superseded. The live contract is `class MyAgent(BaseAgent[None])` from `datarobot_genai.core.agents` with `async def invoke(self, run_agent_input: RunAgentInput) -> InvokeReturn` yielding AG-UI events, plus `agent/register.py` (`@register_per_user_function`) and `agent/workflow.yaml` served by `nat dragent serve`. All 15 Jinja templates generate the wrong shape.

**It hand-rolls a scaffold DataRobot already generates.** The missing `.datarobot/` directory that broke our first real deploy is produced automatically by the App Framework. Reproducing it means permanently chasing a contract DataRobot actively evolves (component repos pushed within the last week).

The common root cause: the tool tries to *be* the compiler backend for a target it doesn't own.

## Core decisions

1. **Recompile, don't rewrite.** Extract a semantic model of the agent; generate against the target from that model.
2. **Own only the frontend.** Extraction is the novel, defensible part. The backend is DataRobot's — use their recipe and their scripts.
3. **LLM for judgment, deterministic code for transformation**, with every LLM claim carrying checkable provenance.
4. **Nothing is silently dropped.** This is the governing invariant.
5. **Pi harness is the only interface.** The CLI is deleted.

---

## Architecture

```
Pi harness  ──  the agent orchestrates, consulting DataRobot skills every run
     │
     ├─ our tools (deterministic)         └─ DataRobot scripts (authoritative)
     │    sr_index      CPG + dataflow         clone_template.py
     │    sr_extract    evidence → IR          select_framework.py
     │    sr_review     IR for human review    setup_template.py
     │    sr_spec       IR → agent_spec.md     list_llm_models.py
     │    sr_verify     ledger + equivalence   rehearsal.py
     │                                          dr dependency check
     │                                          wait_for_running.py
     │                                          diagnose_workload.py
     └─ sr_implement    LLM writes impl into the cloned recipe, verify-loop gated
```

### Layer 1 — Code Property Graph

Extends the existing `pipeline/graph/` package (AST + jedi call edges) with **data flow**. This is the single highest-leverage addition: it converts a whole class of today's guesswork into deterministic answers.

Concretely, reaching-definitions answers what regex cannot:

```python
CLS = ChatOpenAI
model = cfg["model"]
llm = CLS(model=model)      # what actually reaches model= ?
```

The same machinery answers which env vars reach a network call (secret handling), what reaches a prompt (prompt provenance), and which functions perform I/O (tool detection).

### Layer 2 — Semantic probes

Deterministic queries over the CPG, each returning **facts with provenance** (graph node id + `file:line`): reachable callables, values flowing into a parameter, decorated functions, network/filesystem/DB call sites, env reads and their consumers, framework-symbol usage gated on resolved imports.

Probes never guess. If a value can't be resolved statically, they say so — that unresolved fact is what gets escalated to Layer 3.

### Layer 3 — LLM interpretation, evidence-grounded

Input is **evidence bundles from the probes**, not raw source dumps. Output is structured IR claims, each of which must cite the graph nodes supporting it.

Two hard rules:
- A claim whose citation doesn't check out against the CPG is **rejected**, not trusted.
- The LLM may *classify and describe*; it may not invent facts the graph contradicts (e.g. asserting an entry point that isn't reachable).

This is where the long tail lives: "is this function a tool?", "what is this agent for?", "this `_invoke_model()` looks like an LLM call with no shim."

### Layer 4 — Two-layer IR

**Migration IR** (ours, rich, internal). Every element carries `evidence`:

| Section | Contents |
|---|---|
| `entry_points` | module, function, signature, input/output schema |
| `tools` | name, callable, inputs/outputs, description, side effects, auth |
| `llm_calls` | client, provider, **model resolved via dataflow**, params, prompt provenance, migration status |
| `orchestration` | topology kind (sequential / graph / crew / router / custom), nodes, edges |
| `state` | in-memory, persisted, vector stores, backends |
| `external_io` | HTTP, DB, filesystem, vector targets |
| `config` | env vars, required-ness, what they reach |
| `residue` | what could not be carried over, why, severity |
| `coverage` | the ledger (below) |

**`agent_spec.md`** (DataRobot's, interchange). Projected from the Migration IR: `model`, `system_prompt`, `tools[{function_name, inputs, out, auth_spec}]`, `examples`, `frontend`. This is what DR's own toolchain consumes.

The Migration IR exists because `agent_spec.md` is a *greenfield design* artifact with no representation for control-flow topology, state, or prompt provenance. Projecting straight to it would silently discard exactly the behavior that makes complex agents complex.

**The IR is the review surface.** A 200-file agent collapses to a few hundred lines of typed, diffable spec. The user reviews the tool's *understanding*, not a 4,000-line diff.

### Layer 5 — Scaffold from DataRobot, not from us

`clone_template.py` (pins `datarobot-agent-application`) → `select_framework.py` (writes `.datarobot/answers/agent-agent.yml`) → `setup_template.py` (`.env` + Pulumi stack). Framework choice comes from the IR's orchestration topology, not an import-name guess.

This deletes all 15 templates, `config_generator.py`, and `ast_migrate.py`, and fixes the `.datarobot/` deploy gap by construction.

### Layer 6 — Implementation, LLM-written and verifier-gated

The harness agent writes `MyAgent.invoke()`, tools, and `workflow.yaml` wiring into the cloned recipe, working from the Migration IR and the framework guide in `datarobot-agent-assist`.

Trust comes from the **verifier**, not the model. Bounded generate → verify → repair:

1. Python parses; imports resolve; every declared tool exists
2. `dr dependency check` — a documented hard stop we currently never run
3. `rehearsal.py` — simulate the agent from the spec before trusting it
4. Coverage ledger reconciles (below)
5. On failure the LLM proposes a repair; re-verify; bounded retries; then escalate to the human

### Layer 7 — The coverage ledger (the governing invariant)

Deterministic probes enumerate **source facts**: every LLM-ish call site, decorated tool, env read, network call. The IR enumerates **migrated facts**. The ledger reconciles them.

Every source fact must be exactly one of:
- **migrated** — present in the IR and in the generated implementation
- **deferred** — explicitly recorded with a reason
- **blocking** — cannot be represented; migration stops

**Unclassified is impossible by construction.** This is the direct structural answer to the 14-name-regex problem: we will still miss exotic patterns, but we will always *know* we missed them.

Per the fidelity decision: when behavior can't be faithfully carried over, it **blocks with an explanation** rather than emitting a plausible-looking agent that quietly does something different.

### Layer 8 — Deploy and prove it runs

Real lifecycle observation replacing today's fire-and-forget: `wait_for_build.py`, `wait_for_running.py`, `wait_for_replacement.py`, `diagnose_workload.py` on failure, `check_limits.py` as preflight. Optionally `create_use_case.py` + OTel to onboard the migrated agent to DataRobot monitoring.

**Differential equivalence** (the endgame, phased last): record the original agent's behavior against a mocked LLM/network layer, replay the same inputs through the migrated one, diff the traces. This is the only thing that actually *proves* a migration preserved behavior.

---

## What gets deleted

`superrobot/cli.py` · all 15 `superrobot/templates/*.j2` · `pipeline/config_generator.py` · `pipeline/ast_migrate.py` · `pipeline/deployer.py` (Agent App path, superseded) · the `[project.scripts]` entry · `typer` dependency.

Retained and repurposed as tools: `pipeline/graph/*` (extended with dataflow), `pipeline/scanner.py` (demoted to a probe, no longer authoritative for framework choice), `dr/*` clients, `setup/*`, `pipeline/receipts.py`.

Deleting the template layer moots audit findings C16, C17, C18, I34–I40 outright. C8–C12 (scanner detection wrongness) are moot as *authoritative* signals — scanner becomes one evidence source among several, and confidence is derived from evidence quality rather than a static per-name table.

## Non-goals

Multi-language support · deploying anything other than through the DataRobot recipe · preserving the source repo's own structure in the output (we generate a DataRobot-native agent, not a patched copy) · a headless/CI interface (Pi harness only, per decision).

## Risks

- **The recipe is a moving target.** Mitigation: we pin a template version and consume DR's scripts rather than duplicating their contract, so drift is a version bump, not a rewrite.
- **Dataflow analysis is genuinely hard.** Mitigation: intraprocedural reaching-definitions first (covers the observed failure cases); interprocedural only if evidence demands it.
- **LLM-written implementation can be wrong.** Mitigation: it is never trusted — the verifier gates it, and the coverage ledger bounds what can silently escape.
- **`agent_spec.md` may not round-trip complex agents.** Mitigation: exactly why the Migration IR exists as the richer internal layer.
