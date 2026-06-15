# SuperRobot — AGENTS.md

## What It Is

SuperRobot is a TUI-powered CLI for bringing any existing Python agent to DataRobot without
rebuilding it from scratch. It fills the brownfield gap that neither the DR CLI (`dr`) nor
Agent Assist (`mdb`) addresses: both assume you start from a DR template. SuperRobot assumes
you already have a working agent somewhere else and want it running on DR.

**One-liner:** Bring any agent to DataRobot without rebuilding it from scratch.

**Full description:** Point it at any existing Python agent repo (LangChain, LlamaIndex,
raw async, CrewAI outside DR) -> SuperRobot maps it to the closest DR agent framework,
wraps it in the right template structure, generates the Pulumi deployment config and wires
up af-components. Comes with a TUI that renders your agent's execution graph live (workflow
visualization that Agent Assist has on its Milestone 2 roadmap but hasn't shipped), a
generative dr-ui builder (describe a component in plain English, get valid `@dr-ui` React
back), and a one-shot pre-deploy eval that runs before anything touches production.
Greenfield and template modes exist too, but the real gap it fills is: you already have an
agent, and you want it on DR without rebuilding it from scratch.

---

## The Problem It Solves

### Current state of brownfield deployment to DR (as of 2026-06)

Ground truth from internal DR docs. SuperRobot automates every painful step listed here.

**Prerequisites a developer must install manually before doing anything:**
- `task` (Taskfile runner)
- `uv` (Python package manager)
- `pulumi` (infrastructure as code)
- `dr` CLI (Go binary; Homebrew only on macOS; Linux/Windows install undocumented)
- `npm` (for any UI component)

**There is no brownfield migration path in any existing tooling.** To deploy an existing
agent you must:

1. Clone a DR agent template (`dr templates clone`)
2. Rewrite your agent to extend a DR base class (`LangGraphAgent`, `CrewAIAgent`, etc.)
3. Restructure code into `agent/agent/` to comply with DRUM bundle layout
4. Wire `custom.py` entry point with correct `_RUNTIME_PARAM_KEYS`
5. Configure `workflow.yaml` for LLM routing
6. Set up Pulumi `infra/` scripts for every cloud resource
7. Configure runtime parameters in BOTH `infra/infra/agent.py` AND `custom.py`
8. Move system prompts to the Prompt Management Registry (hardcoded prompts require
   full Pulumi redeploy on every prompt change)
9. Audit all imports: DRUM flattens the bundle — `from agent.agent.planner import X`
   fails in production; only flat imports work
10. Wait 15-20 min per deploy (BUZZOK-30076: `time.time()` Pulumi trigger forces `npm
    build` on every deploy even for Python-only changes)
11. If deploy fails: Pulumi deletes deployment and its logs automatically. Only workaround
    is manual UI deploy to preserve logs.

**Evaluation is blocked before production:**
- No `dr push-to-playground` command (PD-2606: open)
- Goal Accuracy, Tool Use, Faithfulness metrics are UI-only, no SDK (PD-2602: open)
- Syftr + LLM Gateway incompatible (PD-2600: open; PD-1552: in implementation)

**SuperRobot automates steps 1-9, warns on 10 and 11, and provides a local pre-deploy
eval as a workaround for the blocked evaluation path.**

---

## Architecture

### What SuperRobot Is NOT

Do not reimplement what already exists:

| System | What it owns | SuperRobot's relationship |
|---|---|---|
| `dr` CLI (Go, Bubble Tea) | Auth, `dr templates clone`, `dr dotenv setup`, `dr run`, `dr component add` | Calls `dr` as subprocess; never reimplements |
| Agent Assist (`mdb`, Python, Pydantic AI) | Greenfield conversational agent design, code gen from spec, scaffolding | Complementary; SuperRobot owns brownfield |
| `recipe-datarobot-agent-templates` | DR framework base classes, Pulumi infra, Taskfile patterns | SuperRobot reads template patterns; generates compliant output |

### What SuperRobot Owns

1. **Brownfield import pipeline** — static analysis of foreign repos → DR framework mapping → compliant generated code
2. **Live TUI with agent graph** — Textual-based full-screen app with interactive DAG pane
3. **Generative dr-ui builder** — natural language → valid `@dr-ui` React components
4. **Pre-deploy evaluation** — inline 5-shot eval before `dr task run deploy` fires
5. **Greenfield + template modes** — thin wrappers delegating to `dr` CLI, unified in same TUI

### Technology Stack

| Layer | Choice | Reason |
|---|---|---|
| TUI framework | [Textual](https://textual.textualize.io/) 0.62+ (Python) | Best Python TUI; async-native; reactive widgets; CSS layout |
| Terminal output | Rich (bundled with Textual) | Tables, syntax highlighting, progress |
| LLM calls | DR LLM Gateway via `openai` async client | Uses DR's own infra; same endpoint as Agent Assist |
| Repo analysis | `ast` + `pathlib` + `pipreqs` | Static analysis, no agent runtime needed |
| Config generation | Pydantic models + Jinja2 templates | Type-safe, testable, templatable |
| UI generation | LLM Gateway + `@dr-ui` component catalog | Prompt includes full component catalog at call time |
| Deploy | Subprocess calls to `dr` CLI + `pulumi` | Reuse existing; never reimplement |
| Graph layout | Custom Textual widget, topological sort + Unicode box-drawing | No external dep; full control over rendering |
| Package manager | `uv` | DR ecosystem standard |
| Testing | `pytest` + `pytest-asyncio` + `pytest-textual-snapshot` | Async-native; Textual widget snapshots |
| Python version | 3.11+ | Matches DR agent template requirement |

### LLM Gateway Integration

```python
from openai import AsyncOpenAI
import os

client = AsyncOpenAI(
    base_url=f"{os.environ['DATAROBOT_ENDPOINT']}/api/v2/genai/llmgw",
    api_key=os.environ["DATAROBOT_API_TOKEN"],
)
```

Default model: `azure/gpt-5-5-2026-04-23`. Override via `SUPERROBOT_MODEL` env var.
All LLM calls are async. Retry: 3 attempts, exponential backoff, 2s base.

### Auth Flow

SuperRobot does NOT implement its own auth. On startup it calls:
```bash
dr auth check
```
If that fails, it tells the user to run `dr auth login` and exits. Auth state lives in
`~/.config/datarobot/drconfig.yaml` managed entirely by the `dr` CLI.

### Prerequisites Check

On every `superrobot` invocation before the TUI launches:

```python
REQUIRED_BINARIES = ["dr", "uv", "task", "pulumi", "node", "npm"]
```

For each missing binary, SuperRobot prints install instructions specific to the user's OS
(`platform.system()`) and exits. This check runs in <100ms (no TUI launch needed).

---

## Three Modes

### Mode 1: Brownfield Import (primary value)

```bash
superrobot import <github-url | local-path>
```

**Pipeline stages (each maps to a TUI step):**

```
Scan → Analyze → Generate → UI Build → Evaluate → Deploy
```

**Stage 1 — Scan (pure static analysis, no LLM, ~1s)**

Would soemthing like graphify make this fast? Or rg?
Produces `ScanResult` Pydantic model:

```python
class ScanResult(BaseModel):
    detected_framework: str          # "langchain" | "llamaindex" | "crewai" | "langgraph" | "pydantic_ai" | "raw_async" | "unknown"
    entry_points: list[EntryPoint]   # file + function name pairs
    dependencies: list[str]          # from requirements.txt / pyproject.toml
    env_vars: list[str]              # discovered via os.getenv / dotenv
    input_signatures: list[str]      # function signatures at entry points
    risk_flags: list[RiskFlag]       # hardcoded secrets, missing .env.example, etc.
    confidence: float                # 0.0-1.0; triggers confirmation dialog if < 0.6
```

Framework detection priority:
1. Import statements (`from langchain`, `from crewai`, etc.)
2. Package presence in `requirements.txt` / `pyproject.toml`
3. `workflow.yaml` presence → NAT
4. README keywords (fallback, low confidence)

**Stage 2 — Analyze (one LLM call)**

Sends `ScanResult` JSON to LLM Gateway. Returns `AnalysisResult`:

```python
class AnalysisResult(BaseModel):
    agent_purpose: str               # one sentence
    dr_framework: DrFramework        # enum: langgraph | crewai | llamaindex | nat | pydantic_ai
    input_schema: dict[str, str]     # field → type
    output_schema: dict[str, str]    # field → type
    suggested_ui_components: list[str]
    missing_requirements: list[str]  # items to warn user about
    risk_flags: list[str]            # security / config issues found
    notes: str                       # uncertainty explanation if confidence < 0.7
    confidence: float
```

**Stage 3 — Generate (deterministic from AnalysisResult, no LLM)**

Generates into a temp directory, then shows diff before writing to target:

| Generated file | Source of truth |
|---|---|
| `agent/agent/custom.py` | DR template pattern; `_RUNTIME_PARAM_KEYS` from env_vars |
| `agent/agent/workflow.yaml` | LLM model config; framework-specific defaults |
| `agent/agent/myagent.py` | `AnalysisResult.dr_framework` → correct base class |
| `infra/infra/agent.py` | Pulumi resource for every env var in `_RUNTIME_PARAM_KEYS` |
| `pyproject.toml` | Merge of detected deps + DR base requirements; never removes |
| `.env.template` | All env vars documented with descriptions |
| `AGENTS.md` | Auto-generated with agent purpose + integration notes |

Critical generation rules enforced (see DR Platform Knowledge section):
- Flat imports only (DRUM bundle)
- Platform API in `DATAROBOT_ENDPOINT`
- Prompt Management Registry pattern (no hardcoded strings)
- `pyproject.toml` additive only
- Runtime params in all three required locations

**Stage 4 — UI Build (optional, LLM call)**

Auto-generates a starter dr-ui React component from `AnalysisResult.input_schema` and
`output_schema`. User can skip or extend via the UI builder modal.

**Stage 5 — Evaluate (local, calls `dr run dev`)**

5 synthetic inputs generated from `agent_purpose`. Each run via subprocess against the
local agent. Pass/fail table shown in `eval_panel.py`. Failures are warnings, not blockers
(explicit `[s]` to skip, `--skip-eval` flag for CI).

**Stage 6 — Deploy (subprocess)**

Calls `dr task run deploy`. Streams output into pipeline panel. On failure:
- Surfaces the "logs deleted" warning proactively before the call
- Parses error from stderr
- Copilot fires a fix suggestion

### Mode 2: Greenfield

```bash
superrobot new
```

TUI wizard asks 3 questions:
1. What does your agent do? (freetext → becomes `agent_purpose`)
2. Which tools does it need? (multi-select from DR skill catalog)
3. Framework? (select; default LangGraph)

Delegates to `dr component add agent` to scaffold the project structure.
Then enters Stage 4 (UI Build) and Stage 6 (Deploy) of the same pipeline.
No Scan or Analyze stages needed (user provides the spec directly).

### Mode 3: From DR Template

```bash
superrobot template
```

Fetches template list via `dr templates list`. TUI shows browsable table with name,
description, framework. Select → `dr templates clone` → opens graph pane showing the
template's existing component structure → user can add UI components → deploy.
No Scan or Analyze stages needed.

---

## TUI Layout

```
┌─ SUPERROBOT v0.1.0 ─────────────────────────────────────────────────────────┐
│  repo: github.com/user/research-agent              [tab] pane  [q] quit      │
├─── PIPELINE ──────────┬─── AGENT GRAPH ──────────────────────────────────────┤
│                       │                                                        │
│  ✓  Scan              │  ┌──────────┐    ┌──────────┐    ┌──────────────┐    │
│  ✓  Analyze           │  │  Input   │──▶ │ LLM Call │──▶ │ Tool:Search  │    │
│  ●  Generate Config   │  └──────────┘    └──────────┘    └──────────────┘    │
│  ○  Build UI          │        │                                 │            │
│  ○  Evaluate          │        ▼                                 ▼            │
│  ○  Deploy            │  ┌──────────┐    ┌──────────┐    ┌──────────────┐    │
│                       │  │  Memory  │──▶ │Synthesize│──▶ │    Output    │    │
│  [enter] continue     │  └──────────┘    └──────────┘    └──────────────┘    │
│  [e] edit config      │                                                        │
│  [r] re-analyze       │  nodes: 6  edges: 7  depth: 3  cost/run: ~$0.004     │
├─── AI COPILOT ────────┴─── CONFIG PREVIEW ─────────────────────────────────────┤
│                       │                                                         │
│  RAG pattern          │  name: research-agent                                   │
│  detected. Retrieval  │  runtime: python3.11                                    │
│  rebuilds index every │  entry: main.py::run_agent                              │
│  cold start. +15-30s  │  dr_framework: langgraph                                │
│  startup latency on   │  skills:                                                │
│  DR. [a] add cache    │    - web_search                                         │
│  skill to fix.        │  resources: cpu: 1  memory: 2Gi                        │
└───────────────────────┴─────────────────────────────────────────────────────────┘
```

### TUI Panels

**Pipeline panel (`tui/pipeline_panel.py`):**
Step tracker. Icons: `○` pending, `●` active (spinning), `✓` done, `✗` failed.
Keyboard shortcuts: `[enter]` continue, `[e]` edit config, `[r]` re-analyze.
Mode-specific: Greenfield shows wizard questions; template mode shows template browser.

**Agent Graph panel (`tui/graph_panel.py`):**
Custom Textual `Widget`. Renders a DAG using Unicode box-drawing characters.
Layout algorithm: topological sort → assign layer (column) per node → position within
column → draw edges with `─`, `│`, `└`, `┐`, `▶`.
Node colors (Textual CSS classes):
- `.node-valid` → green border
- `.node-warning` → yellow border
- `.node-error` → red border
- `.node-ui` → purple border (generated dr-ui components)
- `.node-active` → animated border during live mode

Click a node → opens `NodeDetailModal` with schema, estimated latency, estimated cost.
During deploy: nodes animate from pending → spinning → green as each component goes live.
Post-deploy live mode: last run's execution path highlighted with `.node-active`.

**AI Copilot panel (`tui/copilot_panel.py`):**
Streams LLM Gateway response via `AsyncOpenAI` into a `RichLog` widget.
Context sent per stage:
- Scan: `ScanResult` JSON + stage name
- Analyze: `AnalysisResult` JSON + detected risks
- Generate: diff of generated files + any rule violations caught
- Evaluate: eval results table + failure details
- Deploy: deploy log tail + error if failed

`[a]` applies the suggestion. All changes shown as Rich `Syntax` diff before applying.
Copilot does NOT fire for stages that complete in < 2s (no noise for fast ops).

**Config Preview panel (`tui/config_panel.py`):**
Live `DataTable` + `Syntax` widget showing current generated config.
Tabs: `workflow.yaml` | `myagent.py` | `pyproject.toml` | `.env.template`
`[e]` opens `ConfigEditModal`, `[c]` copies to clipboard, `[v]` runs validation.

**Styles (`tui/app.css`):**
All Textual CSS lives here. Uses DR color tokens where possible.
No hardcoded hex values in Python widget code; use CSS variables.

---

## Superpowers

### Superpower 1: AI Copilot

Context sent to LLM Gateway at each stage (in addition to stage-specific data above):

```
System: You are a DataRobot agent deployment specialist. You are helping a developer
migrate their agent to DataRobot. Be specific and reference the actual code you see.
Suggest only actionable fixes. If you suggest a fix, prefix it with [FIX]:. Keep
responses under 80 words.

User: <stage_name> completed. Here is the current state: <json_context>
```

Rules:
- Never fire a generic tip. Every insight must reference a specific file, line, or value
  from the actual `ScanResult` or `AnalysisResult`
- `[a]` handler calls `config_generator.apply_fix(suggestion)` which re-runs the
  affected Jinja template with the fix applied; shows diff before writing

### Superpower 2: Generative dr-ui Builder

Inline via `[u]` in TUI or standalone via `superrobot ui add "<description>"`.

LLM Gateway prompt structure:
```
System: You are a React/TypeScript developer expert in @dr-ui components.
Generate a single React component using ONLY @dr-ui components from the catalog below.
Use DR design tokens from the token list below. Wire inputs to the provided agent schema.
Return ONLY valid TypeScript JSX. No markdown. No explanation.

<DR_UI_COMPONENT_CATALOG>   ← loaded from superrobot/dr/drui_catalog.json at call time
<DR_DESIGN_TOKENS>          ← loaded from superrobot/dr/drui_tokens.json at call time
<AGENT_INPUT_SCHEMA>        ← from AnalysisResult.input_schema
<AGENT_OUTPUT_SCHEMA>       ← from AnalysisResult.output_schema
<EXISTING_COMPONENTS>       ← already generated components for this session

User: <user_description>
```

`superrobot/dr/drui_catalog.json`: maintained list of all `@dr-ui` component names,
props, and usage examples. Update this file when `@dr-ui` releases new components.
`superrobot/dr/drui_tokens.json`: DR color tokens, spacing tokens, typography tokens.

Output validation: generated TSX parsed with `tree-sitter` or `esprima` (via subprocess);
reject if parse fails and retry once with error context.

Each generated component added to graph pane as a `.node-ui` node connected to the
`Output` node it reads from.

### Superpower 3: Pre-Deploy Evaluation

Cannot be skipped without `--skip-eval` flag or explicit `[s]` in TUI.

```python
class EvalResult(BaseModel):
    run_id: int
    input: str
    output: str | None
    status: Literal["pass", "fail", "error"]
    latency_ms: float
    estimated_cost_usd: float
    failure_reason: str | None       # schema_violation | crash | timeout | incoherent
```

Pass criteria (all must hold):
- No uncaught exception
- Output matches `AnalysisResult.output_schema` structure
- Latency < 30s
- No `None` output

On failure: Copilot fires with the failure reason. `[f]` re-runs eval after applying fix.
This is explicitly a workaround for PD-2606 (no `dr push-to-playground`). It is not a
replacement for DR's native Tensile/Syftr eval pipeline.

### Superpower 4: README Badge Injection

After successful deploy, SuperRobot shows a Rich diff of what it will add to README.md:

```markdown
## Deploy

[![Deploy to DataRobot](https://app.datarobot.com/assets/deploy-badge.svg)](https://app.datarobot.com/deploy?repo=<url>)

### Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| OPENAI_API_KEY | Yes | LLM provider key |
| DATAROBOT_ENDPOINT | auto-set | Injected by DataRobot |
```

Requires explicit `[y]` in TUI before `git commit` + `git push` runs.
If repo has no remote, SuperRobot skips the push and writes the badge locally.

---

## File Structure

```
superrobot/
├── CLAUDE.md                               # This file
├── pyproject.toml
├── uv.lock
├── README.md
├── .env.template
│
├── superrobot/                             # Main package
│   ├── __init__.py
│   ├── cli.py                              # Typer entry point; arg parsing; launches TUI
│   ├── app.py                              # Textual App class; layout; key bindings
│   ├── startup.py                          # Prerequisites check + dr auth check (pre-TUI)
│   │
│   ├── tui/                                # All Textual widgets
│   │   ├── __init__.py
│   │   ├── app.css                         # All Textual CSS; DR tokens as CSS vars
│   │   ├── pipeline_panel.py               # Step tracker widget
│   │   ├── graph_panel.py                  # DAG renderer (custom widget)
│   │   ├── copilot_panel.py                # Streaming copilot RichLog widget
│   │   ├── config_panel.py                 # Config preview tabs widget
│   │   ├── eval_panel.py                   # Eval results DataTable widget
│   │   ├── ui_builder_modal.py             # dr-ui generation modal
│   │   ├── node_detail_modal.py            # Node click → detail modal
│   │   └── config_edit_modal.py            # [e] → edit config modal
│   │
│   ├── pipeline/                           # Core pipeline logic (no Textual deps)
│   │   ├── __init__.py
│   │   ├── scanner.py                      # Static repo analysis; returns ScanResult
│   │   ├── analyzer.py                     # LLM Gateway call; returns AnalysisResult
│   │   ├── config_generator.py             # Deterministic config gen from AnalysisResult
│   │   ├── ui_generator.py                 # dr-ui React gen via LLM
│   │   ├── evaluator.py                    # 5-shot pre-deploy eval
│   │   └── deployer.py                     # Subprocess wrapper: dr task run deploy
│   │
│   ├── dr/                                 # DR platform knowledge + integration
│   │   ├── __init__.py
│   │   ├── cli_wrapper.py                  # All subprocess calls to `dr` binary
│   │   ├── llm_gateway.py                  # AsyncOpenAI client + retry logic
│   │   ├── framework_mapper.py             # Foreign framework → DR framework enum
│   │   ├── drui_catalog.json               # @dr-ui component catalog for UI gen prompts
│   │   ├── drui_tokens.json                # DR design tokens for UI gen prompts
│   │   ├── platform_rules.py               # Encoded DR gotchas as validator functions
│   │   └── prompts/                        # All LLM prompt templates as .txt files
│   │       ├── analyze.txt
│   │       ├── copilot_scan.txt
│   │       ├── copilot_analyze.txt
│   │       ├── copilot_generate.txt
│   │       ├── copilot_evaluate.txt
│   │       ├── copilot_deploy.txt
│   │       └── ui_generate.txt
│   │
│   ├── templates/                          # Jinja2 templates for generated DR files
│   │   ├── custom_py.j2                    # agent/agent/custom.py
│   │   ├── workflow_yaml.j2                # agent/agent/workflow.yaml
│   │   ├── myagent_langgraph.j2            # LangGraph base class variant
│   │   ├── myagent_crewai.j2               # CrewAI base class variant
│   │   ├── myagent_llamaindex.j2           # LlamaIndex base class variant
│   │   ├── myagent_nat.j2                  # NAT base class variant
│   │   ├── myagent_pydanticai.j2           # Pydantic AI base class variant
│   │   ├── infra_agent_py.j2               # infra/infra/agent.py Pulumi resource
│   │   ├── pyproject_toml.j2               # merged pyproject.toml
│   │   ├── env_template.j2                 # .env.template
│   │   └── agents_md.j2                    # AGENTS.md
│   │
│   └── models/                             # Pydantic data models
│       ├── __init__.py
│       ├── scan_result.py                  # ScanResult, EntryPoint, RiskFlag
│       ├── analysis_result.py              # AnalysisResult, DrFramework enum
│       ├── agent_config.py                 # Final merged config ready for templating
│       └── eval_result.py                  # EvalResult, EvalSummary
│
└── tests/
    ├── __init__.py
    ├── unit/
    │   ├── pipeline/
    │   │   ├── test_scanner.py             # Uses fixtures/; no LLM calls
    │   │   ├── test_analyzer.py            # Mocked LLM calls
    │   │   ├── test_config_generator.py    # Snapshot tests of generated files
    │   │   └── test_evaluator.py           # Mocked dr run dev calls
    │   ├── dr/
    │   │   ├── test_framework_mapper.py
    │   │   └── test_platform_rules.py
    │   └── tui/
    │       └── test_graph_panel.py         # Textual snapshot tests
    ├── integration/                        # Marked @pytest.mark.integration
    │   ├── test_full_import_pipeline.py    # Calls real LLM Gateway
    │   └── test_deploy_subprocess.py       # Calls real dr CLI
    └── fixtures/
        ├── langchain_agent/                # Sample foreign repo
        ├── llamaindex_agent/               # Sample foreign repo
        ├── crewai_agent/                   # Sample foreign repo
        └── raw_async_agent/               # Sample foreign repo
```

---

## Commands

```bash
# Primary modes
superrobot import <github-url | path>   # Brownfield: full pipeline
superrobot new                          # Greenfield: wizard → generate → deploy
superrobot template                     # Browse DR templates → customize → deploy

# Individual pipeline stages (useful for debugging / CI)
superrobot scan <path>                  # Stage 1 only; outputs ScanResult JSON to stdout
superrobot analyze <path>               # Stages 1-2; outputs AnalysisResult JSON
superrobot generate <path>              # Stages 1-3; writes generated files to --output-dir
superrobot eval                         # Run 5-shot eval against locally-running agent
superrobot deploy                       # Run deploy against current generated config

# Post-deploy
superrobot live                         # Attach to deployed agent; show live graph
superrobot diff <config-a> <config-b>   # Compare two agent configs side by side

# UI builder (standalone)
superrobot ui add "<description>"       # Generate a dr-ui component

# Flags
--skip-eval                             # Skip pre-deploy eval (not recommended)
--output-dir <path>                     # Override generated file output location
--model <model-name>                    # Override LLM model for this session
--debug                                 # Verbose LLM call logging
--no-tui                                # Plain stdout output (for CI environments)
```

---

## DR Platform Knowledge (Encoded Rules)

Ground truth from `recipe-datarobot-agent-templates` and internal DR docs.
Each rule is a known failure mode. SuperRobot either automates it away or warns.

### DRUM Bundle Rules

DRUM merges `agent/agent/` with `additional_dirs` into a flat bundle at deploy time.

```
Local:  agent/agent/planner.py   →  deployed bundle:  ./planner.py  (flat)
Local:  agent/agent/writer.py    →  deployed bundle:  ./writer.py   (flat)
```

**Rule enforced in `config_generator.py` and `platform_rules.py`:**
Generated imports must always be flat. Never generate:
```python
# WRONG — works locally, fails in deployed DRUM bundle
from agent.agent.planner import PlannerAgent
```
Always generate:
```python
# CORRECT — works in both environments
from planner import PlannerAgent
```

### API Endpoint Rules

Two completely separate APIs. NEVER confuse them.

| API | Env var | Used for |
|---|---|---|
| Platform API | `DATAROBOT_ENDPOINT` | `dr.Client()`, all SDK calls, `dr.Deployment.get()` |
| Prediction API | `DATAROBOT_PREDICTION_API_URL` + `DATAROBOT_KEY` | Real-time model predictions only |

**Rule:** `DATAROBOT_ENDPOINT` must ALWAYS be the Platform API URL.
Using the prediction URL with `dr.Client()` → 404 errors on every SDK call.

### DR Base Classes

Generated `myagent.py` must extend one of these. Import from `datarobot_genai`.
NOTE: verify exact import paths against current `recipe-datarobot-agent-templates`
before generating — these may shift between DR versions.

| Framework | Base class | Confirmed in internal docs |
|---|---|---|
| LangGraph | `LangGraphAgent` | Yes (Agentic Best Practices, Agent Templates Analysis) |
| CrewAI | `CrewAIAgent` | Yes |
| LlamaIndex | `LlamaIndexAgent` | Yes |
| NAT | `NatAgent` | Yes |
| Pydantic AI | `PydanticAIAgent` | Referenced as option; verify import path |

Base classes provide: `self.llm()`, `self.llm(preferred_model=...)`, `self.mcp_tools`,
`self.litellm_api_base(deployment_id)`.

### Prompt Management Rule

**Never hardcode system prompts.** Generated `myagent.py` always uses:
```python
import datarobot as dr
import os

class MyAgent(LangGraphAgent):
    @property
    def system_prompt(self) -> str:
        template = dr.genai.PromptTemplate.get(os.environ["PROMPT_TEMPLATE_ID"])
        return template.get_latest_version().to_fstring()
```

Hardcoded prompts require a full Pulumi redeploy (15-20 min) for every prompt change.
Registry-based prompts update via DR UI with zero redeploy.

### pyproject.toml Rules

- Never remove packages: only additive changes
- Removing base packages breaks Playground ↔ Deployment consistency
- Batch all dep changes: each `pyproject.toml` change triggers full Docker image rebuild (10+ min)

Enforced by `platform_rules.validate_pyproject(original, generated)` which diffs and
raises `PyprojectRemovalError` if any package from `original` is missing from `generated`.

### Runtime Parameter Three-Location Rule

Every env var the agent needs must appear in exactly THREE places:
1. `infra/infra/agent.py` — Pulumi `RuntimeParameter` resource definition
2. `custom.py` `_RUNTIME_PARAM_KEYS` list
3. `.env.template` — documentation with description

Missing any one causes silent runtime failures. `config_generator.py` always writes all
three atomically. `platform_rules.validate_runtime_params(config)` cross-checks all three.

### Framework Mapping (Foreign → DR)

| Detected in scanned repo | Mapped DR framework | Confidence |
|---|---|---|
| `from langchain` imports + `StateGraph` | `langgraph` | High |
| `from langchain` imports without `StateGraph` | `langgraph` | Medium |
| `from crewai` imports | `crewai` | High |
| `from llama_index` imports | `llamaindex` | High |
| `from pydantic_ai` imports | `pydantic_ai` | High |
| `workflow.yaml` present in repo root | `nat` | High |
| Raw `async def` + `aiohttp`/`httpx` only | `langgraph` | Low — confirm with user |
| None of the above | `langgraph` | Low — confirm with user |

Confidence < 0.6 → TUI pauses and asks user to confirm before proceeding to Generate.

### Agent Graph Node Types (for Graph Panel)

```python
class NodeType(str, Enum):
    INPUT = "input"           # Entry point of the agent
    OUTPUT = "output"         # Final response node
    LLM_CALL = "llm_call"    # Any call to an LLM
    TOOL = "tool"             # Tool invocation (search, code exec, etc.)
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    ROUTER = "router"         # Conditional branching node
    SUBAGENT = "subagent"     # A2A call to another agent
    UI = "ui"                 # Generated dr-ui component (visual only)
```

Scanner detects node types by analyzing AST: LLM calls identified by known client
patterns (`ChatOpenAI`, `self.llm()`, `client.chat.completions`), tool calls by
decorator patterns (`@tool`, `@mcp.tool`, function names in known tool lists).

### Known Deploy Gotchas (surfaced as TUI warnings)

| Gotcha | When warned | Tracking |
|---|---|---|
| 15-20 min deploy for Python-only change | Before every deploy | BUZZOK-30076 |
| Logs deleted on Pulumi failure | Before every deploy | N/A |
| Frontend rebuild on every deploy | Before deploy if UI component detected | BUZZOK-30076 |
| Sequential Pulumi provisioning | During deploy (no parallelism) | BUZZOK-30076 |
| Windows not natively supported | Prerequisites check on Windows | BUZZOK-29366 |
| GitHub rate limiting on pulumi-datarobot | During prerequisites check | N/A |

### A2A Sequential Execution Rule

Generated multi-agent code must NEVER use `asyncio.gather` for A2A calls:
```python
# NEVER generate — causes race conditions
results = await asyncio.gather(agent_a.process(task), agent_b.process(task))

# Always generate — sequential is required
result_a = await agent_a.process(task)
result_b = await agent_b.process(task, context=result_a)
```

---

## Development Conventions

### Python Style
- Python 3.11+
- Type hints on all function signatures; no `Any` without comment explaining why
- `ruff` for formatting and linting (`ruff check`, `ruff format`)
- `mypy --strict` for type checking
- `uv` for dependency management

### Error Handling
- No bare `except`
- All LLM Gateway calls: `llm_gateway.py` wrapper handles retries; callers get typed
  results or typed exceptions, never raw `openai` exceptions
- All subprocess calls to `dr` / `pulumi`: capture stdout + stderr; never swallow
- Textual app: all unhandled exceptions caught at App level → displayed in copilot panel
  as user-readable message with `[debug]` toggle for traceback

### Async Patterns
- All LLM calls: `async` via `AsyncOpenAI`
- All subprocess calls: `asyncio.create_subprocess_exec` (never `subprocess.run` — blocks
  Textual event loop)
- All Textual I/O-bound work: `@work(thread=False)` async workers
- CPU-bound work (scanner AST parsing): `@work(thread=True)` to avoid blocking event loop

### LLM Prompt Engineering
- All prompt templates in `superrobot/dr/prompts/*.txt` — never inline in Python
- Every prompt includes: role definition, JSON output schema, at least one negative
  example, explicit uncertainty instruction: "If confidence < 0.7, set confidence field
  accordingly and explain in `notes`"
- All structured LLM output validated against Pydantic models before use
- Never trust LLM output for import paths or class names; validate against known DR
  schemas in `platform_rules.py`
- If LLM output fails Pydantic validation: retry once with the validation error appended
  to the prompt; if still fails: raise `LLMOutputValidationError` with both raw outputs

### Testing Standards
- Required per function: happy path + at least one edge case + at least one failure case
- Scanner tests: use `tests/fixtures/`; zero LLM calls; deterministic
- LLM-dependent tests: `pytest-mock` mocks `llm_gateway.call()`; never hit real gateway
- Integration tests: `@pytest.mark.integration`; run via `uv run pytest -m integration`
- Textual widget tests: `pytest-textual-snapshot` for graph panel rendering

### Commit Convention
```
feat(scanner): detect pydantic-ai entry points
fix(graph): correct edge rendering for self-loop nodes
fix(generator): enforce flat imports in myagent templates
chore(deps): bump textual to 0.62.0
docs(claude): add A2A sequential execution rule
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATAROBOT_API_TOKEN` | Yes | DR API token; used for LLM Gateway + dr auth |
| `DATAROBOT_ENDPOINT` | Yes | Platform API URL (NOT prediction URL) |
| `SUPERROBOT_MODEL` | No | LLM model override (default: `azure/gpt-5-5-2026-04-23`) |
| `SUPERROBOT_DEBUG` | No | `1` = verbose LLM call logging to stderr |
| `SUPERROBOT_SKIP_EVAL` | No | `1` = skip pre-deploy eval (not recommended) |

---

## What SuperRobot Does Not Do

- Does not reimplement `dr templates clone`, `dr dotenv setup`, `dr run`, or auth —
  delegates entirely to the `dr` binary
- Does not provide conversational agent design from scratch (Agent Assist's domain)
- Does not support non-Python agents (JavaScript, Go: out of scope)
- Does not replace DR's native eval tools (Tensile, Syftr, Playground) — local eval is
  a workaround for PD-2606, not a substitute
- Does not manage secrets or inject credentials into the DR image registry
- Does not support air-gapped DR deployments
- Does not generate fine-tuned models or custom execution environments
- Does not work on Windows (BUZZOK-29366 in DR; advise user to use Codespaces or WSL)

---

## Demo Script (6 minutes)

| Time | Action | What audience sees |
|---|---|---|
| 0:00 | `superrobot import https://github.com/user/langchain-agent` | Prerequisites check passes; TUI launches fullscreen |
| 0:30 | Scan completes | Graph pane: 6 nodes appear with edges; pipeline step turns `✓` |
| 1:00 | Analyze completes | Config preview populates; Copilot fires first specific insight |
| 1:45 | Copilot flags DRUM import path issue | Press `[a]`; diff shown; fix applied inline |
| 2:30 | Generate completes | All 6 config files shown in config panel |
| 3:00 | Eval runs | 3/5 pass; failure detail shown; Copilot suggests fix; apply; 5/5 |
| 3:45 | `[u]` → type "results panel with confidence scores" | dr-ui TSX generates live; UI node appears in graph |
| 4:30 | Deploy fires | Graph nodes animate green one-by-one; live URL appears |
| 5:00 | `superrobot live` | Test query sent; graph highlights live execution path |
| 5:45 | README badge diff shown | Press `[y]`; badge committed; click from GitHub to show deploy flow |
| 6:00 | Done | |