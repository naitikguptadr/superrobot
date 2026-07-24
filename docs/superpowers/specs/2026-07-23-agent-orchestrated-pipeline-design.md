# Agent-orchestrated pipeline + shell polish — design

**Date:** 2026-07-23
**Status:** Approved by user, pending implementation plan.

## Goal

Today `superrobot`'s pipeline (scan → transform → validate → deploy → receipts) only
runs as separate headless CLI subcommands. The Pi shell (`shell/`) is just a branded
chat session wired to the DataRobot LLM Gateway — it doesn't know the pipeline exists.

This design makes the Pi shell the primary way to drive the whole pipeline: the user
talks naturally ("import this repo and deploy it"), and the shell's own agent calls
the pipeline stages as tools, narrating progress and gating on confirmation before
anything risky (deploy). It also fixes/upgrades the shell's visual layer: a live
pipeline stage-rail widget, animated in-progress state, and custom tool rendering —
built only on real, verified Pi extension API surface (`pi.registerTool`,
`ctx.ui.setWidget`, `ctx.ui.confirm`), not invented capabilities.

No Swarm integration — confirmed out of scope, consistent with `HANDOFF.md`.

## Non-goals

- Rewriting or restyling the standalone Python `superrobot` CLI's own console output
  (Rich tables/colors already exist per Specs 01–08; this only adds Pi tool wrappers
  around it).
- Anything requiring Pi theme capabilities that don't exist (background color,
  typography, true motion/easing) — `docs/ui-qa.md`'s existing honest scoping stands.
- Building a Swarm client/schema.
- Changing the underlying Gap Analysis / receipts / deploy semantics in the Python
  engine — this is a shell-layer (TypeScript) feature that calls the existing CLI.

## Architecture

### New extension module: `shell/extensions/superrobot/`

Split the current single-file `shell/extensions/superrobot.ts` into a small directory
(existing provider/theme/chip logic moves in unchanged):

```
shell/extensions/superrobot/
├── index.ts          # existing provider/theme/session_start wiring (unchanged logic)
├── cli-bridge.ts      # spawns `superrobot <cmd> --json`, parses/validates JSON, typed errors
├── pipeline-state.ts   # in-memory stage-rail state machine + reducer (pure, testable)
├── tools.ts            # pi.registerTool() definitions: scan/transform/validate/deploy/receipts/memory
├── rail-widget.ts      # renders pipeline-state.ts state to ctx.ui.setWidget() lines via pi-tui Box
└── render.ts           # shared renderCall/renderResult helpers (spinner frames, color helpers)
```

### CLI bridge (`cli-bridge.ts`)

- Runs `superrobot <args> --json` via `pi.exec()` (already available to extensions),
  with `cwd` defaulting to the session's working directory.
- If `superrobot` isn't found on PATH: catch and return a clean typed error (`{ ok:
  false, reason: "not_found" }`) — mirrors the exact fix already shipped in
  `dr/cli_wrapper.py` (Spec 08) for the same failure mode on the Python side. Never
  throw a raw `ENOENT` into the LLM's tool result.
- Parses stdout as JSON; on parse failure, returns the raw stdout/stderr tail (already
  truncated per Pi's 50KB/2000-line rule) as a typed error so the LLM can see what
  actually happened instead of a stack trace.
- One function per subcommand (`scan`, `transform`, `validate`, `deploy`, `receiptShow`,
  `receiptOperations`, `receiptDiagnose`, `receiptReplace`, `memoryEnsure`), each
  returning a discriminated union (`{ ok: true, data: T } | { ok: false, ... }`).

### Pipeline state (`pipeline-state.ts`)

Pure reducer, no Pi/TUI imports — testable with `node:test` in isolation.

```ts
type StageId = "scan" | "transform" | "validate" | "deploy" | "receipt";
type StageStatus = "pending" | "active" | "done" | "failed";
interface StageState { id: StageId; status: StageStatus; detail: string }
type PipelineState = StageState[];

function withStageStarted(state, id): PipelineState
function withStageDone(state, id, detail): PipelineState
function withStageFailed(state, id, detail): PipelineState
function resetPipeline(): PipelineState  // fresh 5-stage, all pending
```

State lives in the extension's closure (like the `Multiple Tools` pattern in Pi's
own docs), reconstructed on `session_start`/reload from the session's tool-result
history the same way the docs' "State Management" example does (scan tool-result
entries for the last known stage detail per stage).

### Stage-rail widget (`rail-widget.ts`)

- Registered via `ctx.ui.setWidget("superrobot-rail", lines)` — updated after every
  pipeline tool call (start, streaming update, completion, failure).
- Box drawn via `pi-tui`'s `Box`/`Container` primitives (unicode-width-aware), never
  hand-padded strings — this is the direct fix for the alignment bug flagged during
  design review (padding raw strings to a fixed character count breaks the moment
  content includes wide glyphs or varies in length).
- Active stage glyph cycles through a small frame set (`◐◓◑◒` or braille spinner)
  on a ~90ms interval, matching the cadence Pi's own `setWorkingIndicator()` uses —
  driven by a `setInterval` inside the tool's `execute()` that calls `onUpdate()`
  (and, for the persistent widget outside the tool row, `ctx.ui.setWidget()` again)
  until the stage resolves. Interval is cleared on tool completion or `signal.abort`.
- On completion: glyph swaps directly from spinner to `✓`/`✗` — a single redraw, no
  synthetic "pop" animation attempted in the terminal (that was a browser-only stand-in
  for the *feel*; real terminal redraws are frame-based glyph swaps, not eased motion).
- Widget is cleared (removed via `ctx.ui.setWidget("superrobot-rail", [])`) once
  receipts are written and no pipeline is in flight, so it doesn't linger as stale
  chrome in unrelated conversation.

### Tools (`tools.ts`)

One `pi.registerTool()` per pipeline stage. Each:
- Has a `promptSnippet` and `promptGuidelines` bullet, per Pi's docs, so the model
  reliably picks the right tool at the right time without generic prompting.
- Calls the matching `cli-bridge.ts` function.
- Updates `pipeline-state.ts` and the rail widget at start/update/end.
- Returns a short `content` summary for the LLM (cite real values: repo, framework,
  finding counts — never generic filler, matching the existing "AI Copilot" rule
  already encoded in the old vision and still a good rule) plus `details` for
  `renderResult`.
- Truncates any large payload per Pi's 50KB/2000-line rule before returning it to
  the LLM (e.g. long file diffs from `transform`).

`superrobot_deploy` specifically:
- Before calling the CLI, runs `ctx.ui.confirm()` showing target, and the two known
  gotchas (BUZZOK-30076 build time, logs-deleted-on-failure) pulled from the same
  strings `platform_rules`/`deployer.py` already print today — not re-invented copy.
- If the user cancels, returns a tool result saying so (no CLI call made, no receipt
  written — matches today's semantics where only actual deploy attempts write
  receipts).
- Never sets `--waive` unless the user's message explicitly asked to waive/override
  a specific finding; the tool's `promptGuidelines` says this explicitly, and the
  system prompt's existing rule ("Gap Analysis findings that are blocking must stop
  deploy. Warnings need explicit waiver.") already backs this up.

### Rendering (`render.ts`)

- Shared color/spinner helpers used by every tool's `renderCall`/`renderResult`.
- Compact default view (one line, colored status), `expanded` view shows Gap Analysis
  findings / generated file list / receipt fields — using `context.expanded` per Pi's
  docs.
- Uses theme tokens already defined in `shell/theme/superrobot.theme.json` (`teal`,
  `gold`, `red`, `green`, `slate`) via `theme.fg(...)`, not new hardcoded hex values.

### System prompt (`shell/prompts/system.md`)

Add golden-path guidance:
- The intended order (scan → transform → validate → deploy → receipt) and that each
  stage's tool should be preferred over shelling out to `bash` + `superrobot` manually.
- Reiterate: never fabricate Gap Analysis/scan findings in narration — quote the
  tool's actual output.
- Reiterate the waiver rule above.

## Data flow example

```
user: "import tests/fixtures/langchain_agent and deploy it as an agent app"
  → superrobot_scan(path) → cli-bridge → `superrobot scan <path> --json`
     → rail: scan active → done ("langchain detected, 3 env vars, conf 0.90")
  → agent narrates specifics from the real ScanResult JSON
  → superrobot_transform(path) → `superrobot transform <path> --json --output-dir <tmp>`
     → rail: transform active → done ("6 files generated")
  → superrobot_validate(dir) → `superrobot validate <dir> --json`
     → rail: validate active → done/failed depending on blocking findings
  → if validate has blocking findings: agent reports them, does NOT call deploy
  → superrobot_deploy(dir, target) → ctx.ui.confirm(...) → `superrobot deploy ... --json`
     → rail: deploy active → done/failed
  → superrobot_receipts("show") → confirms the receipt was written
```

## Error handling

- Every `cli-bridge.ts` function returns typed results; tools never let a raw
  exception reach the LLM except when Pi's own contract requires throwing to signal
  `isError` (per Pi's docs: "throw to signal an error"). In that case throw with a
  clean, specific message (never a raw stack trace) — matches the project's existing
  "No bare except" / typed-exception conventions on the Python side.
- `dr` not on PATH, `superrobot` not on PATH, and JSON parse failures are three
  distinct, distinguishable error paths surfaced to the LLM so it can explain the
  real cause instead of guessing.

## Testing plan

### Automated
- `pipeline-state.ts` and `cli-bridge.ts`'s JSON-parsing/error-typing logic: pure
  functions, unit-tested with Node's built-in `node:test` (no new dependency; nothing
  else in `shell/` uses a test framework today, matching existing conventions).
- `npm run typecheck && npm run build` stay green (already part of the verification
  matrix).
- Existing Python `pytest tests/unit`, `ruff`, `mypy` stay green (no engine changes
  expected).

### Live (manual, with real DataRobot credentials — user already agreed to authenticate)
1. `dr auth login`, then `superrobot setup --endpoint ... --token ... --yes`,
   `superrobot doctor` — confirm real capability probe (Gateway/Agent
   App/Workload/Memory).
2. Launch the shell (`cd shell && npm run build && npm start`), confirm the real
   DataRobot Gateway model loads (not a 401 fallback like the earlier `pi --print`
   smoke test).
3. Drive the golden path conversationally against `tests/fixtures/langchain_agent`:
   scan → transform → validate, confirm the rail widget updates and renders cleanly,
   confirm tool narration cites real scan/gap-analysis data.
4. Reach the deploy confirm gate; a real `dr task run deploy` (Agent App) is a genuine
   15–20 minute production deploy — confirm with the user in the moment whether to
   actually execute it or verify everything up to the confirm dialog and cancel.
5. Exercise `superrobot_memory_ensure` and, if a Workload image is available, the
   Workload deploy path.
6. Visual QA of the rail widget/spinner/box edges needs a human at the live
   interactive terminal (documented limitation, same as `docs/ui-qa.md` — no tool
   here attaches to an interactive TTY) — the user drives step 2–5 directly and
   reports back.

## Open questions for the user (before/at deploy step)

- Whether to let the live test actually fire a real Agent App deploy (15–20 min,
  creates real DataRobot resources) or stop at the confirm gate.
