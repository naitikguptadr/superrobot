# Trying SuperRobot

A walkthrough of the IR migration pipeline on a real repo. Every command
and every output below was run against the current tree — nothing here is
aspirational.

## What it does

You point it at a Python agent repo. It builds a code property graph,
runs deterministic probes over it, and assembles a **Migration IR**: a
typed model of what the agent actually is — entry point, tools, model
calls, control-flow topology, config.

The IR is the review surface. A 200-file agent collapses to a few hundred
lines you can read and diff, instead of a 4,000-line output diff you
can't.

The thing that makes it trustworthy is the **coverage ledger**. Every fact
a probe finds must end up migrated, deferred-with-a-reason, or blocking.
There is no fourth state and no default, so a fact nobody accounted for
shows up as unaccounted and the pipeline stops. Where it can't carry
something over faithfully, it blocks and tells you why rather than
emitting a plausible-looking agent that quietly does something else.

## Setup

```bash
git submodule update --init --recursive   # DataRobot's scripts are vendored
uv sync
```

## The flow

Two interfaces. The Pi harness is the real one; the JSON transport
underneath it is what the walkthrough uses so you can see each step.

### 1. See what it understood

```bash
uv run python -m superrobot.ir report /path/to/agent
```

On `tests/fixtures/langchain_agent`:

```
Coverage: 3 source fact(s), 3 accounted for.

BLOCKING -- migration cannot proceed:
  llm_call:main.py:10:ChatOpenAI (llm_call) at /tmp/srtry/main.py:10 --
  ChatOpenAI(...): ChatOpenAI model could not be resolved statically
  (no model argument at the call site); guessing it would be invisible
  at deploy time

KNOWN LIMITS (not blocking):
  state backends, external I/O, prompt provenance and tool side effects
  were not extracted: no probe exists for them yet; their absence here
  is not evidence of absence
```

That agent constructs `ChatOpenAI(api_key=...)` and names no model, so it
runs on LangChain's default. The migration stops rather than pick one for
you — a wrong model is invisible until it's in production.

`extract` gives you the full IR as JSON instead of the summary:

```bash
uv run python -m superrobot.ir extract /path/to/agent
```

On `tests/fixtures/langgraph_research_agent` that yields:

```
framework: langgraph
entry:     main.run_agent(query: str, max_sources: int = 3) -> dict[str, str | list[str]]
tools:     web_search(query: str, limit: int)
topology:  graph  planner, researcher, writer, START, END
edges:     START->planner, planner->researcher, writer->END
```

The recipe framework comes from the detected topology, not from guessing
at import names.

### 2. Answer the blockers

```bash
uv run python -m superrobot.ir decisions-template /path/to/agent
```

Writes a starter file listing every blocker with its reason. Everything
in it is commented out, so an unattended run can't accidentally
acknowledge anything. Save it as `superrobot-decisions.yaml` and fill in
the ones you want to resolve:

```yaml
model: azure/gpt-4o
system_prompt: |
  You are a research assistant. Given a topic, research it and report findings.
examples:
  - Research the current state of retrieval-augmented generation
facts:
  - fact: "llm_call:main.py:7:Agent"
    disposition: migrated
    reason: crew uses the org default; we are pinning it to gpt-4o on the Gateway
    model: gpt-4o
```

Every decision needs a reason, including `migrated` — the ledger records
it prefixed as a human decision, so the report always distinguishes what
was derived from what someone asserted. A decision naming a fact the
probes no longer produce blocks loudly rather than silently doing
nothing, which is what you want the first time the code moves under a
decisions file you wrote last week.

`system_prompt` has to come from you: no probe extracts one, and
`agent_spec.md` requires it. Writing one on your behalf would change what
the agent does.

### 3. Confirm it's clean

```bash
uv run python -m superrobot.ir report /path/to/agent \
  --decisions /path/to/agent/superrobot-decisions.yaml
```

```
Coverage: 4 source fact(s), 4 accounted for.

No gaps: every source fact is migrated or explicitly deferred.
```

### 4. Project to DataRobot's `agent_spec.md`

```bash
uv run python -m superrobot.ir spec /path/to/agent \
  --decisions /path/to/agent/superrobot-decisions.yaml
```

```yaml
# Migrated from /tmp/srdemo
# Not represented in this spec:
#   - orchestration topology (crew, 2 node(s)) has no agent_spec.md representation
#   - warning: state backends, external I/O, prompt provenance and tool side
#     effects were not extracted
model: gpt-4o
system_prompt: You are a research assistant...
tools: []
examples:
- Research the current state of retrieval-augmented generation
frontend:
  type: chat
```

`agent_spec.md` is a greenfield design format — it has no field for
control-flow topology, state, or prompt provenance. So the projection is
lossy by construction, and what didn't fit rides along as comments on the
artifact rather than living only in our own report.

Run it without `--decisions` and it refuses, listing the blockers.

### 5. Scaffold the recipe

```bash
uv run python -m superrobot.ir scaffold /path/to/output \
  --framework langgraph --llm-model azure/gpt-4o
```

Runs DataRobot's own `clone_template.py`, `select_framework.py`, and
`setup_template.py` — called, never reimplemented. This is what produces
the `.datarobot/` directory whose absence broke the first real deploy.

Needs network access, `dr` on PATH, and DataRobot credentials.

## Through the Pi harness

```bash
cd shell && npm install && npm run build
```

Five tools: `sr_extract`, `sr_report`, `sr_decisions`, `sr_spec`,
`sr_scaffold`. Plug your agent in and ask for a migration; the harness
agent drives them and consults the vendored DataRobot skills as it goes.

Their prompt guidelines say explicitly that a refusal is not an error to
route around. An agent that retries past a block, or hand-writes an
`agent_spec.md` to get moving, defeats the whole point.

## What to expect on your own repo

Honest state, measured on the nine bundled fixtures:

**Works.** Entry points with full signatures. Tools via decorators,
constructors, and `tools=[...]`. LangGraph node/edge topology. CrewAI
agents and tasks. Env vars plus which callables they reach. Model
resolution through aliases, local variables, and config dicts — the cases
regex provably cannot do. Provider recognition across langchain, openai,
anthropic, autogen, haystack, semantic-kernel, smolagents, crewai, and
llamaindex.

**Blocks, correctly.** Framework-default models (crewai, llamaindex,
smolagents name no model). Providers we can't identify. Conditional graph
edges whose targets are chosen at runtime. Any env var whose name is
computed.

**Not built yet.** State backends, external I/O, prompt provenance, and
tool side effects have no probe — the IR says so in `residue` on every
run rather than letting their absence read as absence in the agent. There
is no LLM interpretation layer and no implementation writer: the pipeline
takes you to a reviewed spec and a scaffolded recipe, not to a finished
agent. Those are Phase 2.

Expect blockers on a first run. That's the tool working. The ones that
matter are the ones where you disagree with the reason — those are worth
reporting.
