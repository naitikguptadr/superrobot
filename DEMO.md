# SuperRobot — Presentation & Demo Runbook

> Everything below was verified working on 2026-06-10 against **staging.datarobot.com**
> with GPT-5.5 on the LLM Gateway. 85 unit tests, ruff, and strict mypy all green.

---

## 1. The pitch (60–90 seconds)

**One-liner:** *Bring any existing Python agent to DataRobot without rebuilding it from
scratch.*

**The problem.** Today there is **no brownfield path** to DR. If you have a working
LangChain / LlamaIndex / CrewAI agent, you must manually: clone a template, rewrite your
agent around a DR base class, restructure into the DRUM bundle layout, wire `custom.py`
runtime params, write `workflow.yaml`, write Pulumi infra, and register every env var in
**three separate places** — miss one and it fails silently at runtime. Both existing tools
(`dr` CLI, Agent Assist) assume you're starting from a DR template.

**What SuperRobot does.** Point it at any Python agent repo. It:
1. **Scans** the repo statically (AST — no LLM, ~1s): framework, entry points, env vars, risks
2. **Analyzes** via the DR LLM Gateway: maps to the closest DR framework, infers I/O schemas
3. **Generates + migrates** — every source module is copied into the bundle DRUM-flat with
   imports rewritten, `myagent.process()` calls the real entry point, and **every LLM
   client call is rewired through the DR LLM Gateway** (a generated `dr_llm.py` shim:
   on DR it uses the platform's injected credentials — no OpenAI/Anthropic keys; off DR
   it falls back to the original provider config). Plus the platform gotchas *encoded as
   rules*: flat DRUM imports, the three-location env-var rule, Prompt Registry, additive-only
   `pyproject.toml`
4. **Builds UI**: describe a component in English → valid `@dr-ui` React, with a live
   browser preview
5. **Evaluates** locally before deploy (works around PD-2606 — no `dr push-to-playground`)
6. **Deploys** via the `dr` CLI — it never reimplements what `dr` already does

Plus: a live TUI with the agent's **real execution graph** (for LangGraph repos it parses
the actual `StateGraph` nodes and edges), and an AI copilot that flags issues specific to
*your* code with one-key fixes.

---

## 2. How it works (30 seconds, if asked)

- **Python + Textual** TUI; pipeline logic has zero TUI dependencies (testable headless)
- **LLM calls** go through DR's own LLM Gateway (same infra as Agent Assist), default
  model `azure/gpt-5-5-2026-04-23`, switchable per run with `--model`
- **Static analysis** is pure `ast` — scan never calls an LLM
- **Generation** is deterministic Jinja2 from Pydantic models — the LLM never writes
  config directly; validated against encoded platform rules
- **Auth & deploy** delegate to the `dr` binary — SuperRobot owns the gap, not the stack

---

## 3. Pre-demo checklist (do this 10 min before)

```bash
cd ~/workspace/superrobot
superrobot setup --check        # expect 4 green checks
ls examples/research-agent      # demo repo present
```

- Terminal: **full screen**, dark theme, font ~14pt (TUI needs ≥ 120×35)
- Internet required (LLM Gateway + the UI preview pulls React from CDN)
- If `dr auth` is red: `superrobot setup`, pick **Staging**, press `a` to re-login

---

## 4. The demo flow (~5 minutes)

The demo repo is `examples/research-agent` — a realistic multi-file LangGraph research
agent: planner → researcher → writer StateGraph, conditional routing, two `@tool`
functions, two env vars.

### Beat 1 — Show the problem (20s)

```bash
cat examples/research-agent/graph.py
```

*Say:* "A normal LangGraph agent — works anywhere, knows nothing about DataRobot.
Migrating this by hand is a ~10-step manual process."

### Beat 2 — Launch the pipeline (30s)

```bash
superrobot import examples/research-agent
```

Point at, in order:
- **Header**: connected to **STAGING**, model GPT-5.5
- **Action bar**: always tells you the next step — "this TUI is keyboard-only, the bar
  drives you"
- **Pipeline rail**: Scan finishes instantly with `langchain · 1.00`
- **AGENT GRAPH**: *"This isn't a picture — SuperRobot parsed the actual StateGraph calls:
  planner → researcher → writer, left to right. The blue node is the conditional router
  it recovered from the routing function's return statements."*

### Beat 3 — Analyze + Copilot (45s)

Wait for Analyze ✓ (`→ langgraph · ~0.8`).
- **Copilot panel** streams insights about *this* repo (it will mention SERPAPI_API_KEY,
  missing .env.example — real findings, not canned)
- `[FIX]` suggestions are in yellow; *mention* "press `a` and it applies the fix with a diff"

### Beat 4 — Generate (30s)

Press **`enter`**.
- Config tabs fill with syntax-highlighted `workflow.yaml` / `myagent.py` /
  `pyproject.toml` / `.env.template`
- *Say:* "Every platform gotcha is enforced: flat imports for the DRUM bundle, both env
  vars registered in all three required locations, prompts pulled from the Prompt
  Registry — the stuff that normally fails silently in production."

### Beat 5 — Generative UI + live preview (60s) ← the wow moment

Press **`enter`** → the UI builder modal opens. Type:

```
research report viewer with query input, expandable plan section and final summary card
```

Press **enter**, wait ~10s for the TSX to generate. Then press **`o`**.
- A browser opens with the component **actually running** — type in the input, click the
  button, state updates live
- *Say:* "That's the generated React executing — @dr-ui components shimmed locally so you
  can verify the wiring before it ever touches a DR app build."
- Show the `component.tsx` tab in the TUI too

### Beat 6 — Evaluate (45s)

Back in the terminal, press **`enter`** to run the 5-shot eval (or **`s`** to skip if
short on time).
- *Say:* "SuperRobot migrated the business logic into the bundle — every source module
  copied DRUM-flat with imports rewritten, and `myagent.process()` calls the real entry
  point. The eval executes that migrated code: `dr run dev` first, falling back to direct
  execution. This is the local workaround for PD-2606 — no `dr push-to-playground` exists."
- The research agent needs `langchain`/`langgraph` installed plus an OpenAI key, so on a
  clean machine expect errors — **but point at the Reason column**: it now says exactly
  why (e.g. `crash: ModuleNotFoundError: No module named 'dotenv'`), not just "crash".

**To show a fully GREEN eval** (great closer if time allows — quit and run):

```bash
superrobot eval -p examples/echo-agent
```

The echo agent is stdlib-only, so all 5 runs pass with real outputs — proof the
migrate-then-execute loop genuinely works end to end.

**The strongest proof point (mention it, or run if you have 3 spare minutes):** the
migrated research agent runs its FULL multi-step LangGraph logic — planner LLM call →
web search → writer LLM call — **through the DataRobot LLM Gateway with a fake OpenAI
key**. The generated `dr_llm.py` shim rewires every `ChatOpenAI(...)` call site to DR
credentials. Verified 5/5 green:

```bash
cd examples/research-agent/.superrobot && uv sync          # installs agent deps
cd ../../.. && export OPENAI_API_KEY=not-a-real-key
export SUPERROBOT_EVAL_PYTHON=examples/research-agent/.superrobot/.venv/bin/python
export SUPERROBOT_MODEL=azure/gpt-5-mini-2025-08-07        # fast model for the 30s limit
superrobot eval -p examples/research-agent
```

*Say:* "That's the agent's entire logic executing on DataRobot's LLM infrastructure —
no provider keys, no code changes by hand."

### Beat 7 — Stop at the edge of Deploy (15s)

The action bar now reads *"Press enter to deploy to DataRobot (15–20 min)"*.
- *Say:* "One more enter and this Pulumi-deploys to staging — 15–20 minutes, so I'll spare
  us. Files are already on disk."
- Press **`q`**. Done.

```bash
ls examples/research-agent/.superrobot   # show the artifacts if asked
```

---

## 5. Key cheat-sheet (keep visible during demo)

| Key | Does |
|---|---|
| `enter` | advance pipeline / confirm |
| `u` | open UI builder |
| `o` | open live UI preview in browser |
| `a` | apply copilot [FIX] |
| `s` | skip eval |
| `e` | focus the config editor — type directly in the tab, `ctrl+s` saves to disk |
| `r` | re-analyze |
| `q` | quit (files stay on disk) |
| `escape` | dismiss any modal |

## 6. Command reference (if asked "can it do X?")

```bash
superrobot setup                 # first-run wizard (Production / Staging / custom)
superrobot setup --check         # verify everything
superrobot import <path|url>     # full pipeline (clones GitHub URLs too)
superrobot import x --no-tui     # headless, JSON per stage — CI friendly
superrobot scan <path>           # stage 1 only, instant, no LLM
superrobot analyze <path>        # stages 1–2
superrobot generate <path> -o d  # stages 1–3, writes files
superrobot eval -p <path>        # 5-shot local eval
superrobot ui add "desc" --preview   # standalone UI gen + browser preview
superrobot new / template        # greenfield wizard / DR template browser
--model anthropic/claude-opus-4-8    # swap models per run (113 on the gateway)
```

## 7. Likely questions & answers

- **"Does it touch production?"** No. Configured for staging; deploy only fires on an
  explicit final keypress. Everything else is local + LLM Gateway calls.
- **"What frameworks?"** LangChain, LangGraph (incl. real StateGraph extraction), CrewAI,
  LlamaIndex, Pydantic AI, NAT, raw async. Confidence < 0.6 asks before proceeding.
- **"Does the LLM write my config?"** No — analysis output is validated Pydantic; all
  files come from deterministic templates with platform rules enforced on top.
- **"What about eval metrics like Goal Accuracy?"** Those are UI-only today (PD-2602).
  This local eval is explicitly a pre-deploy smoke gate, not a Tensile/Syftr replacement.
- **"Windows?"** No (BUZZOK-29366 upstream) — WSL/Codespaces.
- **"What's left?"** Business-logic migration is scaffolded-not-automated (the TODO in
  `myagent.py`); `superrobot live` execution-path tracing needs a deployed agent;
  `dr templates clone` isn't in dr v0.2.71 yet so template mode hands off to
  `dr templates setup`.

## 8. If something breaks live

- **Gateway hiccup at Analyze** → quit, rerun with `--model azure/gpt-5-mini-2025-08-07`
  (faster, very reliable)
- **TUI glitches** → fall back to headless and narrate the JSON:
  `superrobot import examples/research-agent --no-tui --skip-eval -o /tmp/demo`
- **UI preview won't open** → `open <output>/.superrobot/ui/preview.html` manually
- **Total fallback** → screenshots of every stage are in `/tmp/sr-*.png` from QA runs
