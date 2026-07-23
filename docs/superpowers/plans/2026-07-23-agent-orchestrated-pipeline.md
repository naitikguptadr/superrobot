# Agent-Orchestrated Pipeline + Shell Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Pi shell drive the SuperRobot pipeline (scan → transform →
validate → deploy → receipts) conversationally, with a live pipeline stage-rail
widget and a confirm gate before deploy — built only on real, verified Pi
extension API surface.

**Architecture:** Split the single `shell/extensions/superrobot.ts` into a
directory of small, independently testable modules: a CLI bridge that shells out
to `superrobot <cmd> --json` with typed results, a pure pipeline-state reducer, a
unicode-width-safe box-drawing helper, a color/spinner helper backed by the real
theme JSON, a rail-widget renderer, and a tools module registering one
`pi.registerTool()` per pipeline stage. Existing provider/theme/session_start
wiring moves unchanged into the new directory's `index.ts`.

**Tech Stack:** TypeScript (Node 20+, `NodeNext` module resolution), Pi's
extension API (`@mariozechner/pi-coding-agent`), `@mariozechner/pi-tui`'s
`visibleWidth`, `typebox` + `StringEnum` for tool parameter schemas, Node's
built-in `node:test` runner (no new test dependency — matches the fact that
nothing in `shell/` uses a test framework today).

**Reference:** Design spec at
`docs/superpowers/specs/2026-07-23-agent-orchestrated-pipeline-design.md`.

---

## Before you start

Confirm the shell's dependencies are installed (they were installed once during
design research, but re-run to be safe):

```bash
cd /Users/naitik.gupta/workspace/superrobot/shell && npm install
```

Confirm Node's built-in test runner works on `.ts` files directly on this
machine's Node version (no ts-node/tsx needed):

```bash
node --version   # expect v22+ (verified working on v24.16.0)
```

---

### Task 1: Restructure the extension into a directory

**Files:**
- Create: `shell/extensions/superrobot/index.ts` (moved content from the file below)
- Delete: `shell/extensions/superrobot.ts`
- Modify: `shell/extensions/tsconfig.json`
- Modify: `shell/src/cli.ts:24` (extension path)

This is a pure move — no behavior change. It unlocks the multi-file layout the
rest of this plan needs.

- [ ] **Step 1: Create the directory and move the file verbatim**

```bash
cd /Users/naitik.gupta/workspace/superrobot/shell/extensions
mkdir superrobot
git mv superrobot.ts superrobot/index.ts
```

- [ ] **Step 2: Widen the extensions tsconfig to include subdirectories**

Current `shell/extensions/tsconfig.json` has `"include": ["*.ts"]`, which only
matches top-level files. Change it to match files at any depth:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "noEmit": true
  },
  "include": ["**/*.ts"]
}
```

- [ ] **Step 3: Point `cli.ts` at the new entry point**

In `shell/src/cli.ts`, find:

```ts
const extensionPath = join(__dirname, "..", "extensions", "superrobot.ts");
```

Change to:

```ts
const extensionPath = join(__dirname, "..", "extensions", "superrobot", "index.ts");
```

- [ ] **Step 4: Verify build and typecheck still pass**

```bash
cd /Users/naitik.gupta/workspace/superrobot/shell
npm run typecheck
npm run build
```

Expected: both succeed with no errors (this step only moved a file and updated
two paths — no logic changed yet).

- [ ] **Step 5: Commit**

```bash
git add shell/extensions/superrobot/index.ts shell/extensions/tsconfig.json shell/src/cli.ts
git status --short  # confirm superrobot.ts shows as deleted (via the git mv)
git commit -m "refactor(shell): move superrobot extension into its own directory"
```

---

### Task 2: CLI bridge — typed wrapper around the `superrobot` binary

**Files:**
- Create: `shell/extensions/superrobot/cli-bridge.ts`
- Test: `shell/extensions/superrobot/cli-bridge.test.ts`

This is the single place that knows how to invoke `superrobot <cmd> --json` and
turn its output into a typed result. Every pipeline tool calls through this —
no tool ever calls `pi.exec` directly.

Exact CLI argument shapes (from `superrobot/cli.py`), confirmed by reading the
command definitions directly:
- `superrobot scan <source> --json`
- `superrobot transform <source> [--output-dir DIR] [--skip-eval] --json`
- `superrobot validate <path> [--source REPO] --json` — **exits 1 when there are
  blocking findings, but still emits a valid `GapReport` JSON body.** This is a
  normal domain outcome, not a broken CLI call.
- `superrobot deploy <path> --target agent-app|workload [--image-uri URI] [--waive] --json`
  — **also exits non-zero on a Gap-Analysis-blocked deploy while still emitting
  JSON** (`{success: false, blocked_by_gap_analysis: true, findings: [...]}`).
- `superrobot receipt show [<id>] --json`
- `superrobot receipt operations [--target T] --json`
- `superrobot receipt diagnose <id> --json`
- `superrobot receipt replace <id> [--waive] --json`
- `superrobot memory ensure <name> --json`

Because a non-zero exit code can mean either "the CLI itself failed to run" or
"the CLI ran fine and is reporting a blocked/failed domain outcome", the result
type carries both the JSON payload (when parseable) and a distinction between
those two cases.

- [ ] **Step 1: Write the failing tests**

```typescript
// shell/extensions/superrobot/cli-bridge.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { createCliBridge, type ExecFn } from "./cli-bridge.js";

function fakeExec(
  impl: (args: string[]) => { stdout: string; stderr: string; code: number },
): ExecFn {
  return async (args: string[]) => impl(args);
}

test("scan: builds args and parses JSON on success", async () => {
  let capturedArgs: string[] = [];
  const exec = fakeExec((args) => {
    capturedArgs = args;
    return { stdout: JSON.stringify({ detected_framework: "langchain" }), stderr: "", code: 0 };
  });
  const cli = createCliBridge(exec);
  const result = await cli.scan("tests/fixtures/langchain_agent");
  assert.deepEqual(capturedArgs, ["scan", "tests/fixtures/langchain_agent", "--json"]);
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal((result.data as { detected_framework: string }).detected_framework, "langchain");
  }
});

test("validate: non-zero exit with valid JSON is a domain result, not an error", async () => {
  const exec = fakeExec(() => ({
    stdout: JSON.stringify({
      findings: [{ rule: "flat_imports", severity: "blocking", message: "bad import" }],
    }),
    stderr: "",
    code: 1,
  }));
  const cli = createCliBridge(exec);
  const result = await cli.validate("/tmp/sr-out");
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.reason, "cli_error");
    assert.ok(result.data, "data should still be attached for a domain-level non-zero exit");
  }
});

test("exec throwing ENOENT is reported as not_found", async () => {
  const exec: ExecFn = async () => {
    throw new Error("spawn superrobot ENOENT");
  };
  const cli = createCliBridge(exec);
  const result = await cli.scan("some/path");
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.reason, "not_found");
});

test("non-JSON stdout is reported as parse_error", async () => {
  const exec = fakeExec(() => ({ stdout: "not json", stderr: "", code: 0 }));
  const cli = createCliBridge(exec);
  const result = await cli.scan("some/path");
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.reason, "parse_error");
});

test("deploy: builds target/waive/image-uri flags", async () => {
  let capturedArgs: string[] = [];
  const exec = fakeExec((args) => {
    capturedArgs = args;
    return { stdout: JSON.stringify({ success: true }), stderr: "", code: 0 };
  });
  const cli = createCliBridge(exec);
  await cli.deploy("/tmp/sr-out", "workload", { imageUri: "registry/img:tag", waive: true });
  assert.deepEqual(capturedArgs, [
    "deploy",
    "/tmp/sr-out",
    "--target",
    "workload",
    "--image-uri",
    "registry/img:tag",
    "--waive",
    "--json",
  ]);
});

test("receipts: show/operations/diagnose/replace build distinct arg shapes", async () => {
  const seen: string[][] = [];
  const exec = fakeExec((args) => {
    seen.push(args);
    return { stdout: JSON.stringify({}), stderr: "", code: 0 };
  });
  const cli = createCliBridge(exec);
  await cli.receiptShow();
  await cli.receiptShow("abc123");
  await cli.receiptOperations("agent-app");
  await cli.receiptDiagnose("abc123");
  await cli.receiptReplace("abc123");
  assert.deepEqual(seen, [
    ["receipt", "show", "--json"],
    ["receipt", "show", "abc123", "--json"],
    ["receipt", "operations", "--target", "agent-app", "--json"],
    ["receipt", "diagnose", "abc123", "--json"],
    ["receipt", "replace", "abc123", "--json"],
  ]);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/naitik.gupta/workspace/superrobot/shell
node --test extensions/superrobot/cli-bridge.test.ts
```

Expected: FAIL — `Cannot find module './cli-bridge.js'` (file doesn't exist yet).

- [ ] **Step 3: Implement `cli-bridge.ts`**

```typescript
// shell/extensions/superrobot/cli-bridge.ts

export type ExecFn = (
  args: string[],
  opts?: { cwd?: string },
) => Promise<{ stdout: string; stderr: string; code: number }>;

export type CliResult<T> =
  | { ok: true; data: T }
  | { ok: false; reason: "not_found"; message: string }
  | { ok: false; reason: "parse_error"; message: string }
  | { ok: false; reason: "cli_error"; message: string; data?: T };

const MAX_ERROR_TAIL = 2000;

async function runJson<T>(
  exec: ExecFn,
  args: string[],
  opts?: { cwd?: string },
): Promise<CliResult<T>> {
  let raw: { stdout: string; stderr: string; code: number };
  try {
    raw = await exec([...args, "--json"], opts);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    if (message.includes("ENOENT")) {
      return { ok: false, reason: "not_found", message: "superrobot CLI not found on PATH" };
    }
    return { ok: false, reason: "cli_error", message };
  }

  let parsed: T | undefined;
  try {
    parsed = JSON.parse(raw.stdout) as T;
  } catch {
    const tail = (raw.stderr || raw.stdout).slice(-MAX_ERROR_TAIL);
    return { ok: false, reason: "parse_error", message: `superrobot did not return JSON: ${tail}` };
  }

  if (raw.code !== 0) {
    return {
      ok: false,
      reason: "cli_error",
      message: raw.stderr.slice(-MAX_ERROR_TAIL) || `superrobot exited with code ${raw.code}`,
      data: parsed,
    };
  }
  return { ok: true, data: parsed };
}

export function createCliBridge(exec: ExecFn) {
  return {
    scan(path: string, cwd?: string) {
      return runJson<unknown>(exec, ["scan", path], { cwd });
    },

    transform(path: string, opts: { outputDir?: string; skipEval?: boolean } = {}, cwd?: string) {
      const args = ["transform", path];
      if (opts.outputDir) args.push("--output-dir", opts.outputDir);
      if (opts.skipEval) args.push("--skip-eval");
      return runJson<unknown>(exec, args, { cwd });
    },

    validate(path: string, source?: string, cwd?: string) {
      const args = ["validate", path];
      if (source) args.push("--source", source);
      return runJson<unknown>(exec, args, { cwd });
    },

    deploy(
      path: string,
      target: "agent-app" | "workload",
      opts: { imageUri?: string; waive?: boolean } = {},
      cwd?: string,
    ) {
      const args = ["deploy", path, "--target", target];
      if (opts.imageUri) args.push("--image-uri", opts.imageUri);
      if (opts.waive) args.push("--waive");
      return runJson<unknown>(exec, args, { cwd });
    },

    receiptShow(id?: string, cwd?: string) {
      const args = ["receipt", "show"];
      if (id) args.push(id);
      return runJson<unknown>(exec, args, { cwd });
    },

    receiptOperations(target?: string, cwd?: string) {
      const args = ["receipt", "operations"];
      if (target) args.push("--target", target);
      return runJson<unknown>(exec, args, { cwd });
    },

    receiptDiagnose(id: string, cwd?: string) {
      return runJson<unknown>(exec, ["receipt", "diagnose", id], { cwd });
    },

    receiptReplace(id: string, cwd?: string) {
      return runJson<unknown>(exec, ["receipt", "replace", id], { cwd });
    },

    memoryEnsure(name: string, cwd?: string) {
      return runJson<unknown>(exec, ["memory", "ensure", name], { cwd });
    },
  };
}

export type CliBridge = ReturnType<typeof createCliBridge>;
```

Note: `runJson` always appends `--json` itself (every test above passes args
*without* a trailing `--json` into the fake exec's assertion because the helper
adds it) — re-check the test's `assert.deepEqual` expectations include `--json`
as the last element, which matches `[...args, "--json"]` above.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
node --test extensions/superrobot/cli-bridge.test.ts
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Typecheck**

```bash
npm run typecheck
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add extensions/superrobot/cli-bridge.ts extensions/superrobot/cli-bridge.test.ts
git commit -m "feat(shell): add typed CLI bridge for the superrobot binary"
```

---

### Task 3: Pipeline state — pure reducer

**Files:**
- Create: `shell/extensions/superrobot/pipeline-state.ts`
- Test: `shell/extensions/superrobot/pipeline-state.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// shell/extensions/superrobot/pipeline-state.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { freshPipeline, withStageActive, withStageDone, withStageFailed } from "./pipeline-state.js";

test("freshPipeline starts all five stages pending", () => {
  const state = freshPipeline();
  assert.equal(state.length, 5);
  assert.deepEqual(state.map((s) => s.id), ["scan", "transform", "validate", "deploy", "receipt"]);
  assert.ok(state.every((s) => s.status === "pending"));
});

test("withStageActive only changes the targeted stage", () => {
  const state = freshPipeline();
  const next = withStageActive(state, "transform", "running...");
  assert.equal(next.find((s) => s.id === "transform")?.status, "active");
  assert.equal(next.find((s) => s.id === "transform")?.detail, "running...");
  assert.equal(next.find((s) => s.id === "scan")?.status, "pending");
});

test("withStageDone and withStageFailed set status and detail", () => {
  const state = freshPipeline();
  const done = withStageDone(state, "scan", "langchain detected");
  assert.equal(done.find((s) => s.id === "scan")?.status, "done");
  const failed = withStageFailed(state, "scan", "boom");
  assert.equal(failed.find((s) => s.id === "scan")?.status, "failed");
  assert.equal(failed.find((s) => s.id === "scan")?.detail, "boom");
});

test("reducers do not mutate the input array", () => {
  const state = freshPipeline();
  const snapshot = JSON.stringify(state);
  withStageActive(state, "scan", "x");
  assert.equal(JSON.stringify(state), snapshot);
});
```

- [ ] **Step 2: Run to verify failure**

```bash
node --test extensions/superrobot/pipeline-state.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```typescript
// shell/extensions/superrobot/pipeline-state.ts

export type StageId = "scan" | "transform" | "validate" | "deploy" | "receipt";
export type StageStatus = "pending" | "active" | "done" | "failed";

export interface StageState {
  id: StageId;
  status: StageStatus;
  detail: string;
}

export type PipelineState = StageState[];

const STAGE_ORDER: StageId[] = ["scan", "transform", "validate", "deploy", "receipt"];

export function freshPipeline(): PipelineState {
  return STAGE_ORDER.map((id) => ({ id, status: "pending", detail: "" }));
}

function updateStage(
  state: PipelineState,
  id: StageId,
  status: StageStatus,
  detail: string,
): PipelineState {
  return state.map((stage) => (stage.id === id ? { ...stage, status, detail } : stage));
}

export function withStageActive(state: PipelineState, id: StageId, detail = ""): PipelineState {
  return updateStage(state, id, "active", detail);
}

export function withStageDone(state: PipelineState, id: StageId, detail: string): PipelineState {
  return updateStage(state, id, "done", detail);
}

export function withStageFailed(state: PipelineState, id: StageId, detail: string): PipelineState {
  return updateStage(state, id, "failed", detail);
}

export function hasActiveStage(state: PipelineState): boolean {
  return state.some((stage) => stage.status === "active");
}
```

- [ ] **Step 4: Run to verify pass**

```bash
node --test extensions/superrobot/pipeline-state.test.ts
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add extensions/superrobot/pipeline-state.ts extensions/superrobot/pipeline-state.test.ts
git commit -m "feat(shell): add pure pipeline-state reducer"
```

---

### Task 4: Box drawing — unicode-width-safe, no hand-padded strings

**Files:**
- Create: `shell/extensions/superrobot/box.ts`
- Test: `shell/extensions/superrobot/box.test.ts`

This is the direct fix for the box-edge misalignment flagged during design
review. Width is computed from `visibleWidth()` (unicode- and ANSI-aware, from
`@mariozechner/pi-tui`), never from `.length` or hand-counted padding.

- [ ] **Step 1: Write the failing tests**

```typescript
// shell/extensions/superrobot/box.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { visibleWidth } from "@mariozechner/pi-tui";
import { boxLines } from "./box.js";

test("all lines in the box have equal visible width", () => {
  const lines = boxLines(["short", "a much longer row of text", "mid"]);
  const widths = new Set(lines.map((l) => visibleWidth(l)));
  assert.equal(widths.size, 1, `expected one uniform width, got: ${[...widths]}`);
});

test("box has a top border, one row per input line, and a bottom border", () => {
  const lines = boxLines(["a", "b", "c"]);
  assert.equal(lines.length, 5); // top + 3 rows + bottom
  assert.ok(lines[0].startsWith("┌"));
  assert.ok(lines[0].endsWith("┐"));
  assert.ok(lines[lines.length - 1].startsWith("└"));
  assert.ok(lines[lines.length - 1].endsWith("┘"));
});

test("rows with embedded ANSI color codes still align by visible width", () => {
  const colored = `\x1b[38;2;61;219;217mred herring\x1b[0m`; // visually 11 chars, longer as a raw string
  const lines = boxLines([colored, "short"]);
  const widths = new Set(lines.slice(1, -1).map((l) => visibleWidth(l)));
  assert.equal(widths.size, 1, "ANSI codes must not be counted as visible width");
});

test("minWidth pads narrower content up to the requested width", () => {
  const lines = boxLines(["hi"], 20);
  assert.ok(visibleWidth(lines[1]) >= 20);
});
```

- [ ] **Step 2: Run to verify failure**

```bash
node --test extensions/superrobot/box.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```typescript
// shell/extensions/superrobot/box.ts
import { visibleWidth } from "@mariozechner/pi-tui";

/**
 * Wrap plain or ANSI-colored rows in a box whose borders are computed from the
 * widest row's *visible* width -- never hand-padded, so embedded color codes
 * or unicode glyphs can't drift the edges out of alignment.
 */
export function boxLines(rows: string[], minWidth = 0): string[] {
  const width = Math.max(minWidth, 0, ...rows.map((row) => visibleWidth(row)));
  const pad = (row: string): string => row + " ".repeat(Math.max(0, width - visibleWidth(row)));
  const top = `┌${"─".repeat(width + 2)}┐`;
  const bottom = `└${"─".repeat(width + 2)}┘`;
  const body = rows.map((row) => `│ ${pad(row)} │`);
  return [top, ...body, bottom];
}
```

- [ ] **Step 4: Run to verify pass**

```bash
node --test extensions/superrobot/box.test.ts
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add extensions/superrobot/box.ts extensions/superrobot/box.test.ts
git commit -m "feat(shell): add unicode-width-safe box drawing helper"
```

---

### Task 5: Render helpers — theme colors and spinner frames

**Files:**
- Create: `shell/extensions/superrobot/render.ts`
- Test: `shell/extensions/superrobot/render.test.ts`

Colors are read from the real `shell/theme/superrobot.theme.json` at runtime
(single source of truth — no duplicated hex constants), because widget text is
built outside `renderCall`/`renderResult`, so Pi's `Theme` object (which is only
passed into those two render slots) isn't available where the rail widget draws.

- [ ] **Step 1: Write the failing tests**

```typescript
// shell/extensions/superrobot/render.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { railColor, spinnerFrame } from "./render.js";

test("railColor wraps text in a truecolor ANSI escape and resets after", () => {
  const result = railColor("teal", "hello");
  assert.ok(result.startsWith("\x1b[38;2;"));
  assert.ok(result.endsWith("\x1b[0m"));
  assert.ok(result.includes("hello"));
});

test("railColor falls back to plain text for an unknown color name", () => {
  // @ts-expect-error -- deliberately testing runtime behavior for an invalid name
  const result = railColor("not-a-color", "hello");
  assert.equal(result, "hello");
});

test("spinnerFrame cycles through frames as elapsed time increases", () => {
  const frame0 = spinnerFrame(0);
  const frame1 = spinnerFrame(90);
  const frame2 = spinnerFrame(180);
  const frame4 = spinnerFrame(360); // exactly one full cycle later at 4 frames * 90ms
  assert.notEqual(frame0, frame1);
  assert.notEqual(frame1, frame2);
  assert.equal(frame0, frame4);
});
```

- [ ] **Step 2: Run to verify failure**

```bash
node --test extensions/superrobot/render.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```typescript
// shell/extensions/superrobot/render.ts
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
// extensions/superrobot/render.ts -> up two levels to shell/, then theme/
const THEME_PATH = join(__dirname, "..", "..", "theme", "superrobot.theme.json");

export type RailColorName = "teal" | "tealMuted" | "gold" | "green" | "red" | "slate";

function loadThemeVars(): Record<string, string> {
  const parsed = JSON.parse(readFileSync(THEME_PATH, "utf8")) as { vars: Record<string, string> };
  return parsed.vars;
}

const THEME_VARS = loadThemeVars();

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace("#", "");
  return [
    parseInt(clean.slice(0, 2), 16),
    parseInt(clean.slice(2, 4), 16),
    parseInt(clean.slice(4, 6), 16),
  ];
}

export function railColor(name: RailColorName, text: string): string {
  const hex = THEME_VARS[name];
  if (!hex) return text;
  const [r, g, b] = hexToRgb(hex);
  return `\x1b[38;2;${r};${g};${b}m${text}\x1b[0m`;
}

const SPINNER_FRAMES = ["◐", "◓", "◑", "◒"];

/** Braille-style spinner frame for the given elapsed time, ~90ms per frame -- matches the cadence Pi's own setWorkingIndicator() uses. */
export function spinnerFrame(elapsedMs: number, intervalMs = 90): string {
  const index = Math.floor(elapsedMs / intervalMs) % SPINNER_FRAMES.length;
  return SPINNER_FRAMES[index];
}
```

- [ ] **Step 4: Run to verify pass**

```bash
node --test extensions/superrobot/render.test.ts
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add extensions/superrobot/render.ts extensions/superrobot/render.test.ts
git commit -m "feat(shell): add theme-backed color and spinner helpers"
```

---

### Task 6: Rail widget — pure renderer + live controller

**Files:**
- Create: `shell/extensions/superrobot/rail-widget.ts`
- Test: `shell/extensions/superrobot/rail-widget.test.ts`

`renderRailLines` is pure and unit-tested. `createRailController` wires it to
`ctx.ui.setWidget()` on a timer and is exercised by the live/manual test in
Task 10 (it touches real timers and the real `ctx.ui`, which isn't worth faking
here).

- [ ] **Step 1: Write the failing tests for the pure renderer**

```typescript
// shell/extensions/superrobot/rail-widget.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { visibleWidth } from "@mariozechner/pi-tui";
import { freshPipeline, withStageActive, withStageDone } from "./pipeline-state.js";
import { renderRailLines } from "./rail-widget.js";

test("renders one row per stage plus a label and box borders", () => {
  const state = freshPipeline();
  const lines = renderRailLines(state, 0);
  // 1 label line + top border + 5 stage rows + bottom border
  assert.equal(lines.length, 8);
});

test("every box row shares the same visible width", () => {
  let state = freshPipeline();
  state = withStageDone(state, "scan", "langchain detected, 3 env vars, conf 0.90");
  state = withStageActive(state, "transform", "generating files...");
  const lines = renderRailLines(state, 45);
  const boxRows = lines.slice(2, -1); // skip label + top border, skip bottom border
  const widths = new Set(boxRows.map((l) => visibleWidth(l)));
  assert.equal(widths.size, 1, `expected uniform width, got: ${[...widths]}`);
});

test("stage labels appear in a stable, human-readable order", () => {
  const state = freshPipeline();
  const lines = renderRailLines(state, 0).join("\n");
  const scanIdx = lines.indexOf("Scan");
  const transformIdx = lines.indexOf("Transform");
  const validateIdx = lines.indexOf("Validate");
  const deployIdx = lines.indexOf("Deploy");
  const receiptIdx = lines.indexOf("Receipt");
  assert.ok(scanIdx < transformIdx);
  assert.ok(transformIdx < validateIdx);
  assert.ok(validateIdx < deployIdx);
  assert.ok(deployIdx < receiptIdx);
});
```

- [ ] **Step 2: Run to verify failure**

```bash
node --test extensions/superrobot/rail-widget.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```typescript
// shell/extensions/superrobot/rail-widget.ts
import type { ExtensionContext } from "@mariozechner/pi-coding-agent";
import { boxLines } from "./box.js";
import type { PipelineState, StageId } from "./pipeline-state.js";
import { railColor, spinnerFrame } from "./render.js";

const STAGE_LABELS: Record<StageId, string> = {
  scan: "Scan",
  transform: "Transform",
  validate: "Validate",
  deploy: "Deploy",
  receipt: "Receipt",
};

const WIDGET_KEY = "superrobot-rail";

function stageRow(stage: PipelineState[number], elapsedMs: number): string {
  const label = STAGE_LABELS[stage.id].padEnd(10);
  let glyph: string;
  switch (stage.status) {
    case "done":
      glyph = railColor("green", "✓");
      break;
    case "failed":
      glyph = railColor("red", "✗");
      break;
    case "active":
      glyph = railColor("gold", spinnerFrame(elapsedMs));
      break;
    default:
      glyph = railColor("slate", "○");
  }
  const detail = stage.detail ? railColor("slate", stage.detail) : "";
  return `${glyph} ${label}${detail}`;
}

export function renderRailLines(state: PipelineState, elapsedMs: number): string[] {
  const rows = state.map((stage) => stageRow(stage, elapsedMs));
  return [railColor("slate", "PIPELINE"), ...boxLines(rows)];
}

export interface RailController {
  start(state: PipelineState): void;
  update(state: PipelineState): void;
  stop(): void;
}

/** Drives ctx.ui.setWidget() on a ~90ms tick while a stage is active, so the spinner glyph animates. */
export function createRailController(ctx: ExtensionContext): RailController {
  let interval: ReturnType<typeof setInterval> | undefined;
  let startedAt = 0;
  let currentState: PipelineState | undefined;

  function draw(): void {
    if (!currentState) return;
    ctx.ui.setWidget(WIDGET_KEY, renderRailLines(currentState, Date.now() - startedAt));
  }

  return {
    start(state: PipelineState) {
      currentState = state;
      startedAt = Date.now();
      draw();
      if (!interval) interval = setInterval(draw, 90);
    },
    update(state: PipelineState) {
      currentState = state;
      draw();
    },
    stop() {
      if (interval) {
        clearInterval(interval);
        interval = undefined;
      }
      currentState = undefined;
      ctx.ui.setWidget(WIDGET_KEY, undefined);
    },
  };
}
```

- [ ] **Step 4: Run to verify pass**

```bash
node --test extensions/superrobot/rail-widget.test.ts
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Typecheck**

```bash
npm run typecheck
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add extensions/superrobot/rail-widget.ts extensions/superrobot/rail-widget.test.ts
git commit -m "feat(shell): add pipeline stage-rail widget renderer and controller"
```

---

### Task 7: Register the pipeline tools

**Files:**
- Create: `shell/extensions/superrobot/tools.ts`
- Modify: `shell/extensions/superrobot/index.ts` (call `registerSuperRobotTools(pi)`)

No unit test for this file: it's the integration point between real `pi`/`ctx`
objects and the pure modules above, which are already covered. It's exercised
by the live walkthrough in Task 10. Type-correctness is enforced by
`npm run typecheck`.

- [ ] **Step 1: Implement `tools.ts`**

```typescript
// shell/extensions/superrobot/tools.ts
import { StringEnum } from "@mariozechner/pi-ai";
import type { ExtensionAPI, ExtensionContext } from "@mariozechner/pi-coding-agent";
import { Type } from "typebox";
import { createCliBridge, type CliResult } from "./cli-bridge.js";
import {
  freshPipeline,
  withStageActive,
  withStageDone,
  withStageFailed,
  type PipelineState,
} from "./pipeline-state.js";
import { createRailController, type RailController } from "./rail-widget.js";

interface ScanResult {
  detected_framework: string;
  confidence: number;
  env_vars?: string[];
}

interface DeployResult {
  success: boolean;
  error_message?: string;
  blocked_by_gap_analysis?: boolean;
}

interface GapFinding {
  rule: string;
  severity: "blocking" | "warning";
  message: string;
  file?: string | null;
}

interface GapReport {
  findings: GapFinding[];
}

export function registerSuperRobotTools(pi: ExtensionAPI): void {
  const cli = createCliBridge((args, opts) => pi.exec("superrobot", args, opts));

  let pipeline: PipelineState = freshPipeline();
  let rail: RailController | undefined;

  function railFor(ctx: ExtensionContext): RailController {
    if (!rail) rail = createRailController(ctx);
    return rail;
  }

  pi.registerTool({
    name: "superrobot_scan",
    label: "SuperRobot Scan",
    description:
      "Statically scan a Python agent repo (local path) for framework, entry points, env vars, and risk flags. Always the first step when importing a brownfield agent.",
    promptSnippet: "Scan a brownfield agent repo before transforming it",
    promptGuidelines: [
      "Use superrobot_scan first whenever the user asks to import, migrate, or bring an existing agent repo to DataRobot.",
    ],
    parameters: Type.Object({
      path: Type.String({ description: "Local path to the agent repo" }),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const rc = railFor(ctx);
      pipeline = freshPipeline();
      pipeline = withStageActive(pipeline, "scan", params.path);
      rc.start(pipeline);

      const result = await cli.scan(params.path);
      if (!result.ok) {
        pipeline = withStageFailed(pipeline, "scan", result.message);
        rc.update(pipeline);
        throw new Error(`superrobot scan failed: ${result.message}`);
      }
      const data = result.data as ScanResult;
      const detail = `${data.detected_framework} detected, ${(data.env_vars ?? []).length} env vars, conf ${data.confidence.toFixed(2)}`;
      pipeline = withStageDone(pipeline, "scan", detail);
      rc.update(pipeline);
      return { content: [{ type: "text", text: detail }], details: data };
    },
  });

  pi.registerTool({
    name: "superrobot_transform",
    label: "SuperRobot Transform",
    description:
      "Run Scan -> Analyze -> Generate for a brownfield agent repo, writing a DR-compliant package to outputDir.",
    promptSnippet: "Generate a DataRobot-compliant package from a scanned repo",
    promptGuidelines: [
      "Use superrobot_transform after superrobot_scan succeeds, before superrobot_validate.",
    ],
    parameters: Type.Object({
      path: Type.String({ description: "Local path to the agent repo" }),
      outputDir: Type.String({ description: "Directory to write the generated package into" }),
      skipEval: Type.Optional(Type.Boolean({ description: "Skip the 5-shot eval stage" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const rc = railFor(ctx);
      pipeline = withStageActive(pipeline, "transform", params.outputDir);
      rc.update(pipeline);

      const result = await cli.transform(params.path, {
        outputDir: params.outputDir,
        skipEval: params.skipEval,
      });
      if (!result.ok) {
        pipeline = withStageFailed(pipeline, "transform", result.message);
        rc.update(pipeline);
        throw new Error(`superrobot transform failed: ${result.message}`);
      }
      const data = result.data as { files?: string[] };
      const detail = `${(data.files ?? []).length} files generated`;
      pipeline = withStageDone(pipeline, "transform", detail);
      rc.update(pipeline);
      return { content: [{ type: "text", text: detail }], details: data };
    },
  });

  pi.registerTool({
    name: "superrobot_validate",
    label: "SuperRobot Validate",
    description:
      "Run Gap Analysis against a generated package. Reports blocking and warning findings. A non-zero exit with findings is a normal result, not a tool failure.",
    promptSnippet: "Run Gap Analysis on a generated package before deploying",
    promptGuidelines: [
      "Use superrobot_validate after superrobot_transform and before superrobot_deploy.",
      "If superrobot_validate reports blocking findings, report them to the user and do not call superrobot_deploy unless the user explicitly asks to waive a specific finding.",
    ],
    parameters: Type.Object({
      dir: Type.String({ description: "Generated package directory" }),
      source: Type.Optional(Type.String({ description: "Original repo path, enables the pyproject-removal check" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const rc = railFor(ctx);
      pipeline = withStageActive(pipeline, "validate", params.dir);
      rc.update(pipeline);

      const result = await cli.validate(params.dir, params.source);
      // validate exits non-zero when there are blocking findings -- that's a
      // real GapReport, not a broken CLI call. Narrow explicitly: only
      // not_found/parse_error are genuine tool failures; cli_error carries a
      // GapReport we still want to read.
      let data: GapReport | undefined;
      if (result.ok) {
        data = result.data as GapReport;
      } else if (result.reason === "cli_error") {
        data = result.data as GapReport | undefined;
      } else {
        pipeline = withStageFailed(pipeline, "validate", result.message);
        rc.update(pipeline);
        throw new Error(`superrobot validate failed: ${result.message}`);
      }
      const findings = data?.findings ?? [];
      const blocking = findings.filter((f) => f.severity === "blocking").length;
      const warnings = findings.filter((f) => f.severity === "warning").length;
      const detail = blocking > 0 ? `${blocking} blocking, ${warnings} warning(s)` : `clean (${warnings} warning(s))`;
      pipeline = blocking > 0 ? withStageFailed(pipeline, "validate", detail) : withStageDone(pipeline, "validate", detail);
      rc.update(pipeline);
      return { content: [{ type: "text", text: detail }], details: data ?? {} };
    },
  });

  pi.registerTool({
    name: "superrobot_deploy",
    label: "SuperRobot Deploy",
    description:
      "Deploy a generated package to Agent App or Workload API. Always confirms with the user before running, and surfaces the known BUZZOK-30076 build-time and logs-deleted-on-failure warnings.",
    promptSnippet: "Deploy a validated package to Agent App or Workload",
    promptGuidelines: [
      "Use superrobot_deploy only after superrobot_validate reports zero blocking findings.",
      "Never set waive on superrobot_deploy unless the user explicitly asked to waive or override a specific Gap Analysis finding.",
    ],
    parameters: Type.Object({
      dir: Type.String({ description: "Generated package directory" }),
      target: StringEnum(["agent-app", "workload"] as const),
      imageUri: Type.Optional(Type.String({ description: "Built container image URI, required for target=workload" })),
      waive: Type.Optional(Type.Boolean({ description: "Only true if the user explicitly asked to waive a blocking Gap Analysis finding" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const confirmed = await ctx.ui.confirm(
        `Deploy -> ${params.target}`,
        `Deploy ${params.dir} to ${params.target}. This build takes 15-20 minutes (BUZZOK-30076), ` +
          "and Pulumi deletes logs automatically if the deploy fails. Proceed?",
      );
      if (!confirmed) {
        return { content: [{ type: "text", text: "Deploy cancelled by user; no receipt was written." }], details: { cancelled: true } };
      }

      const rc = railFor(ctx);
      pipeline = withStageActive(pipeline, "deploy", params.target);
      rc.update(pipeline);

      const result = await cli.deploy(params.dir, params.target, {
        imageUri: params.imageUri,
        waive: params.waive,
      });
      // Same narrowing as superrobot_validate: only not_found/parse_error are
      // genuine tool failures; cli_error (e.g. Gap-Analysis-blocked deploy)
      // carries a DeployResult we still want to read.
      let data: DeployResult | undefined;
      if (result.ok) {
        data = result.data as DeployResult;
      } else if (result.reason === "cli_error") {
        data = result.data as DeployResult | undefined;
      } else {
        pipeline = withStageFailed(pipeline, "deploy", result.message);
        rc.update(pipeline);
        throw new Error(`superrobot deploy failed: ${result.message}`);
      }
      const succeeded = data?.success ?? false;
      const detail = succeeded ? "deployed" : (data?.error_message ?? "blocked or failed");
      pipeline = succeeded ? withStageDone(pipeline, "deploy", detail) : withStageFailed(pipeline, "deploy", detail);
      rc.update(pipeline);
      return { content: [{ type: "text", text: detail }], details: data ?? {} };
    },
  });

  pi.registerTool({
    name: "superrobot_receipts",
    label: "SuperRobot Receipts",
    description: "Read deploy receipt history: show a receipt, list operations, diagnose a failure, or replace a prior deploy.",
    promptSnippet: "Inspect or act on deploy receipt history",
    promptGuidelines: [
      "Use superrobot_receipts with action=show right after a deploy to confirm a receipt was written.",
    ],
    parameters: Type.Object({
      action: StringEnum(["show", "operations", "diagnose", "replace"] as const),
      id: Type.Optional(Type.String({ description: "Receipt id, required for diagnose/replace, optional for show" })),
      target: Type.Optional(Type.String({ description: "Filter by target for action=operations" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const rc = railFor(ctx);
      let result: CliResult<unknown>;
      switch (params.action) {
        case "show":
          result = await cli.receiptShow(params.id);
          break;
        case "operations":
          result = await cli.receiptOperations(params.target);
          break;
        case "diagnose":
          if (!params.id) throw new Error("action=diagnose requires id");
          result = await cli.receiptDiagnose(params.id);
          break;
        case "replace":
          if (!params.id) throw new Error("action=replace requires id");
          result = await cli.receiptReplace(params.id);
          break;
        default:
          throw new Error(`unknown receipts action: ${String(params.action)}`);
      }
      if (!result.ok) {
        throw new Error(`superrobot receipt ${params.action} failed: ${result.message}`);
      }
      if (params.action === "show" || params.action === "replace") {
        pipeline = withStageDone(pipeline, "receipt", params.action);
        rc.update(pipeline);
      }
      return { content: [{ type: "text", text: JSON.stringify(result.data) }], details: result.data as object };
    },
  });

  pi.registerTool({
    name: "superrobot_memory_ensure",
    label: "SuperRobot Memory Ensure",
    description: "Idempotently ensure a named DataRobot Memory API space exists.",
    promptSnippet: "Ensure a Memory API space exists for an agent",
    promptGuidelines: [
      "Use superrobot_memory_ensure only when the user's agent needs persistent memory and the Memory capability chip is on.",
    ],
    parameters: Type.Object({
      name: Type.String({ description: "Memory space name" }),
    }),
    async execute(_toolCallId, params) {
      const result = await cli.memoryEnsure(params.name);
      if (!result.ok) {
        throw new Error(`superrobot memory ensure failed: ${result.message}`);
      }
      return { content: [{ type: "text", text: JSON.stringify(result.data) }], details: result.data as object };
    },
  });
}
```

- [ ] **Step 2: Wire it into `index.ts`**

In `shell/extensions/superrobot/index.ts`, add near the top:

```ts
import { registerSuperRobotTools } from "./tools.js";
```

And inside the default export function, alongside the existing
`pi.registerProvider(...)` / `pi.on("session_start", ...)` calls, add:

```ts
registerSuperRobotTools(pi);
```

- [ ] **Step 3: Typecheck and build**

```bash
cd /Users/naitik.gupta/workspace/superrobot/shell
npm run typecheck
npm run build
```

Expected: both succeed. Fix any type errors surfaced here before moving on —
this is the first point where all the modules compose together.

- [ ] **Step 4: Smoke-test that the extension still loads**

```bash
npx --yes @mariozechner/pi-coding-agent --print -e extensions/superrobot/index.ts --system-prompt "test" "say hi" 2>&1 | tail -30
```

Expected: no extension load errors (a Gateway auth error/401 is fine and
expected without real credentials — that's the same result the original
Spec 02 smoke test got; a stack trace naming `tools.ts`/`cli-bridge.ts`/etc. is
not).

- [ ] **Step 5: Commit**

```bash
git add extensions/superrobot/tools.ts extensions/superrobot/index.ts
git commit -m "feat(shell): register pipeline tools (scan/transform/validate/deploy/receipts/memory)"
```

---

### Task 8: System prompt — golden-path and waiver guidance

**Files:**
- Modify: `shell/prompts/system.md`

- [ ] **Step 1: Add golden-path guidance**

Append to `shell/prompts/system.md` (after the existing "Rules:" list):

```markdown
Pipeline tools:
- The golden path is superrobot_scan -> superrobot_transform -> superrobot_validate -> superrobot_deploy -> superrobot_receipts. Prefer these tools over shelling out to `bash` + `superrobot` manually.
- Always narrate specifics from each tool's actual output (framework detected, file counts, real finding messages) -- never generic filler like "looks good" without citing what you found.
- Do not call superrobot_deploy if superrobot_validate reported blocking findings, unless the user explicitly asks to waive a specific one.
```

- [ ] **Step 2: Verify the file still parses as expected (no test harness for prompts — visually confirm)**

```bash
cat /Users/naitik.gupta/workspace/superrobot/shell/prompts/system.md
```

Expected: the new "Pipeline tools:" section appears after the existing "Rules:"
section, nothing else changed.

- [ ] **Step 3: Commit**

```bash
git add shell/prompts/system.md
git commit -m "docs(shell): add golden-path and waiver guidance to the system prompt"
```

---

### Task 9: Test script wiring

**Files:**
- Modify: `shell/package.json`

- [ ] **Step 1: Add a `test` script**

In `shell/package.json`'s `"scripts"` block, add:

```json
"test": "node --test extensions/superrobot/*.test.ts"
```

Full `scripts` block after the change:

```json
"scripts": {
  "build": "tsc -p tsconfig.json",
  "start": "node dist/cli.js",
  "typecheck": "tsc -p tsconfig.json --noEmit && tsc -p extensions/tsconfig.json",
  "test": "node --test extensions/superrobot/*.test.ts"
}
```

- [ ] **Step 2: Run it**

```bash
cd /Users/naitik.gupta/workspace/superrobot/shell
npm run test
```

Expected: all tests from Tasks 2–6 run and pass (cli-bridge, pipeline-state,
box, render, rail-widget — roughly 20 tests total).

- [ ] **Step 3: Commit**

```bash
git add shell/package.json
git commit -m "chore(shell): add npm test script for the extension's unit tests"
```

---

### Task 10: Full automated verification pass

**Files:** none (verification only)

- [ ] **Step 1: Python side stays green (no engine changes expected)**

```bash
cd /Users/naitik.gupta/workspace/superrobot
uv run ruff check .
uv run ruff format --check .
uv run mypy superrobot
uv run pytest tests/unit -q
```

Expected: same as the pre-existing baseline (ruff/mypy clean; pytest all pass
except the already-known environment-only flake in
`test_memory_ensure_blocked_without_auth`, which reads real
`DATAROBOT_API_TOKEN`/`DATAROBOT_ENDPOINT` from this shell's env — unrelated to
this plan's changes).

- [ ] **Step 2: Shell side full check**

```bash
cd /Users/naitik.gupta/workspace/superrobot/shell
npm run typecheck
npm run build
npm run test
```

Expected: all three succeed.

- [ ] **Step 3: Commit any fixes found during this pass**

If any step above fails, fix it, re-run, and commit:

```bash
git add -A
git commit -m "fix(shell): address issues found during full verification pass"
```

(Skip this step entirely if nothing failed.)

---

### Task 11: Live verification with real DataRobot credentials

**Files:** none (manual verification)

This task mixes agent-run setup steps with steps that need the user at a live
interactive terminal (no tool in this environment attaches to an interactive
TTY — same documented limitation as `docs/ui-qa.md`).

- [ ] **Step 1 (agent): Confirm auth and capability probe**

```bash
dr auth login   # user completes this interactively if not already done
cd /Users/naitik.gupta/workspace/superrobot
uv run superrobot setup --endpoint https://app.datarobot.com --token "$DATAROBOT_API_TOKEN" --yes
uv run superrobot doctor
```

Expected: `doctor` exits 0 and reports real capability flags (Gateway, and
whichever of Agent App / Workload / Memory this account is entitled to).

- [ ] **Step 2 (agent): Build the shell and confirm the real Gateway model loads**

```bash
cd shell && npm run build
DATAROBOT_ENDPOINT=https://app.datarobot.com DATAROBOT_API_TOKEN="$DATAROBOT_API_TOKEN" \
  npx --yes @mariozechner/pi-coding-agent --print -e extensions/superrobot/index.ts \
  --system-prompt "$(cat prompts/system.md)" "say hi" 2>&1 | tail -30
```

Expected: no 401 this time (real token) — a real short reply from the DR
Gateway model.

- [ ] **Step 3 (user, interactive): Drive the golden path conversationally**

Hand off to the user:

> Run `cd shell && npm start` in your own terminal. Try: "import
> tests/fixtures/langchain_agent, transform it, and validate it" and confirm:
> - the pipeline rail widget appears and updates through scan → transform → validate
> - the spinner animates on the active stage
> - box edges look clean (no ragged borders)
> - the agent's narration cites real scan/Gap Analysis output, not generic text

- [ ] **Step 4 (user, interactive): Reach the deploy confirm gate**

> Ask it to deploy to Agent App. Confirm the boxed confirm dialog appears with
> the BUZZOK-30076 and logs-deleted warnings. At that point, tell me whether to
> actually confirm the deploy (real 15–20 min build, creates real DataRobot
> resources) or cancel once we've seen the gate render correctly.

- [ ] **Step 5 (agent, after the user's decision): Confirm a receipt was written either way**

```bash
uv run superrobot receipt operations --json
```

Expected: the most recent receipt reflects whatever happened in Step 4
(`blocked`, `deployed`, or `failed`).

- [ ] **Step 6 (agent, if a Workload image and/or Memory entitlement are available): Exercise those paths too**

```bash
uv run superrobot memory ensure demo-space --json
# Only if a built/pushed image is available:
uv run superrobot deploy /tmp/sr-out --target workload --image-uri <uri> --json
```

- [ ] **Step 7: Record what actually got exercised**

Update `docs/verification-matrix.md` or `docs/ui-qa.md` with what step 3–6
actually confirmed live (mirroring the existing honest-scoping style already
used in those docs — real vs. not-yet-verified, not aspirational claims).

```bash
git add docs/verification-matrix.md docs/ui-qa.md
git commit -m "docs: record live verification of the agent-orchestrated pipeline"
```

---

## Self-review notes

- **Spec coverage:** CLI bridge (Task 2), pipeline state (Task 3), box-edge fix
  (Task 4), theme-backed color/spinner (Task 5), rail widget (Task 6), all six
  tools + confirm gate (Task 7), system prompt guidance (Task 8), test wiring
  (Task 9), full automated verification (Task 10), live verification with the
  explicit deploy-or-cancel decision point (Task 11) — every section of the
  design spec has a corresponding task.
- **No Swarm work appears anywhere in this plan** — confirmed out of scope per
  the design doc and the user's explicit instruction.
