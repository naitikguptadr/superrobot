# DataRobot-Styled UI Companion (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional, DataRobot-styled web UI companion (React + `@datarobot/design-system`) that mirrors the existing terminal rail-widget's pipeline status (scan/transform/validate/deploy/receipts) as a second, opt-in renderer — never a requirement for any pipeline stage to function.

**Architecture:** A new `shell/companion/` Vite+React app renders the existing `PipelineState` (already defined in `shell/extensions/superrobot/pipeline-state.ts`) using DataRobot's real `Stepper`/`Badge` components. A new `web-controller.ts` in the shell extension serves that built app over a plain Node `http.createServer` and pushes state updates over a `ws` WebSocket, mirroring the existing `RailController`'s `start`/`update`/`stop` interface exactly so `tools.ts` needs minimal changes to notify both UIs.

**Tech Stack:** React 18+, `@datarobot/design-system` (real, verified npm package — private DataRobot registry), Vite, `react-i18next`/`i18next` (required by the design-system's own bootstrap), Node's stdlib `http`, `ws` (WebSocket server).

**Spec:** `docs/superpowers/specs/2026-07-24-graph-based-pipeline-engine-design.md` (Phase 2 section)

**Known unverified detail (flagged honestly, not guessed):** `@datarobot/design-system`'s `Stepper` component is confirmed to exist with `StepperProps { steps: Step[], onClick, activeKey, isDisabled }`, and `Badge` is confirmed with exact boolean status props (`success`, `error`, `info`, `warning`, `plain`, `isLoading`, etc. — see Task 3). The exact shape of the `Step` type itself (field names for a step's label/key) was NOT captured during research — earlier attempts to install and inspect it directly were blocked by the safety classifier as an unprompted external-package install. **Task 1's first step requires the implementer to install the real package and read its actual type declarations before writing any `Stepper`-consuming code** — do not guess the `Step` shape, read it from `node_modules/@datarobot/design-system/stepper/index.d.ts` (or equivalent) directly.

---

### Task 1: Companion app scaffold + verify the real design-system API

**Files:**
- Create: `shell/companion/package.json`
- Create: `shell/companion/vite.config.ts`
- Create: `shell/companion/tsconfig.json`
- Create: `shell/companion/index.html`
- Create: `shell/companion/src/main.tsx`

- [ ] **Step 1: Scaffold the Vite React TypeScript app**

Run, from the repo root:
```bash
cd shell
npm create vite@latest companion -- --template react-ts
```
This creates `shell/companion/` with a standard Vite+React+TS starter. Delete the generated `src/App.css`, `src/index.css`, and `src/assets/` — the companion will use the design-system's own stylesheet instead, not Vite's default styling.

- [ ] **Step 2: Install real dependencies**

```bash
cd shell/companion
npm install @datarobot/design-system react-i18next i18next
```
This is a real, legitimate project dependency install (not an exploratory scratch install) — it's adding the actual dependency this task needs, declared in `package.json`.

- [ ] **Step 3: Read the actual Stepper and Badge type declarations**

Run:
```bash
cat node_modules/@datarobot/design-system/stepper/index.d.ts
cat node_modules/@datarobot/design-system/badge/index.d.ts
cat node_modules/@datarobot/design-system/granular-progress-bar/index.d.ts
```

Write down (as a comment at the top of the file you create in Task 2) the EXACT `Step` interface fields you find (e.g. does a step have `key`, `label`, `status`, `disabled`? what type is `activeKey`?) and the exact `BadgeProps` fields. Do not proceed to Task 2 until you've confirmed these from the real `.d.ts` files, not from memory or assumption.

- [ ] **Step 4: Confirm the dev build works**

```bash
npm run build
```
Expected: builds successfully with no errors (the default Vite starter content, before any design-system usage, should build cleanly — this just proves the toolchain is wired up before adding real components in Task 2).

- [ ] **Step 5: Commit**

```bash
cd /path/to/repo/root
git add shell/companion/
git commit -m "feat: scaffold companion Vite+React app, verify design-system API"
```

(If `shell/companion/node_modules/` or `dist/` would be committed, add `shell/companion/node_modules/` and `shell/companion/dist/` to the repo's root `.gitignore` first, then re-run `git add`.)

---

### Task 2: Status-to-Badge mapping (pure, testable function)

**Files:**
- Create: `shell/companion/src/status-mapping.ts`
- Test: `shell/companion/src/status-mapping.test.ts`

This task extracts the one piece of genuinely testable business logic — mapping the existing `StageStatus` values to `Badge`'s real boolean props — as a pure function, before touching any React rendering.

- [ ] **Step 1: Write the failing test**

Create `shell/companion/src/status-mapping.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { badgePropsForStatus } from "./status-mapping";

describe("badgePropsForStatus", () => {
  it("maps pending to a plain badge", () => {
    expect(badgePropsForStatus("pending")).toEqual({ plain: true });
  });

  it("maps active to an info badge with a loading indicator", () => {
    expect(badgePropsForStatus("active")).toEqual({ info: true, isLoading: true });
  });

  it("maps done to a success badge", () => {
    expect(badgePropsForStatus("done")).toEqual({ success: true });
  });

  it("maps failed to an error badge", () => {
    expect(badgePropsForStatus("failed")).toEqual({ error: true });
  });
});
```

- [ ] **Step 2: Install vitest and add a test script**

```bash
cd shell/companion
npm install --save-dev vitest @testing-library/react @testing-library/jest-dom jsdom
```

Add to `shell/companion/package.json`'s `"scripts"`:
```json
"test": "vitest run"
```

Add a `shell/companion/vitest.config.ts`:
```typescript
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
  },
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm run test`
Expected: FAIL with a module-not-found error for `./status-mapping`

- [ ] **Step 4: Create the type definitions and implementation**

Create `shell/companion/src/pipeline-types.ts` (mirrors `shell/extensions/superrobot/pipeline-state.ts` verbatim — duplicated deliberately rather than imported cross-package, since the companion is built as a fully separate Vite app with its own module resolution; this is a small, stable, 6-line type definition, not worth cross-package path aliasing complexity):

```typescript
export type StageId = "scan" | "transform" | "validate" | "deploy" | "receipt";
export type StageStatus = "pending" | "active" | "done" | "failed";

export interface StageState {
  id: StageId;
  status: StageStatus;
  detail: string;
}

export type PipelineState = StageState[];
```

Create `shell/companion/src/status-mapping.ts`:

```typescript
import type { StageStatus } from "./pipeline-types";

export interface BadgeStatusProps {
  plain?: boolean;
  info?: boolean;
  success?: boolean;
  error?: boolean;
  isLoading?: boolean;
}

/** Map the existing 4-value StageStatus to @datarobot/design-system's
 * Badge boolean status props (verified: Badge has no single `status`
 * enum prop, it's boolean flags -- success/error/info/warning/plain).
 */
export function badgePropsForStatus(status: StageStatus): BadgeStatusProps {
  switch (status) {
    case "pending":
      return { plain: true };
    case "active":
      return { info: true, isLoading: true };
    case "done":
      return { success: true };
    case "failed":
      return { error: true };
  }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add shell/companion/src/pipeline-types.ts shell/companion/src/status-mapping.ts shell/companion/src/status-mapping.test.ts shell/companion/vitest.config.ts shell/companion/package.json
git commit -m "feat: pure status-to-Badge-props mapping function + tests"
```

---

### Task 3: Pipeline stage list component using real Stepper + Badge

**Files:**
- Create: `shell/companion/src/PipelineView.tsx`
- Test: `shell/companion/src/PipelineView.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `shell/companion/src/PipelineView.test.tsx`:

```typescript
import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PipelineView } from "./PipelineView";
import type { PipelineState } from "./pipeline-types";

const SAMPLE_STATE: PipelineState = [
  { id: "scan", status: "done", detail: "langchain detected, 2 env vars, conf 0.85" },
  { id: "transform", status: "done", detail: "12 files generated" },
  { id: "validate", status: "active", detail: "" },
  { id: "deploy", status: "pending", detail: "" },
  { id: "receipt", status: "pending", detail: "" },
];

describe("PipelineView", () => {
  it("renders a detail message for a completed stage", () => {
    render(<PipelineView state={SAMPLE_STATE} />);
    expect(screen.getByText(/langchain detected/i)).toBeInTheDocument();
  });

  it("renders all 5 stage ids", () => {
    render(<PipelineView state={SAMPLE_STATE} />);
    for (const id of ["scan", "transform", "validate", "deploy", "receipt"]) {
      expect(screen.getByText(new RegExp(id, "i"))).toBeInTheDocument();
    }
  });

  it("renders an empty state message when given no stages", () => {
    render(<PipelineView state={[]} />);
    expect(screen.getByText(/no pipeline activity yet/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test`
Expected: FAIL with a module-not-found error for `./PipelineView`

- [ ] **Step 3: Implement `PipelineView.tsx`**

Using the EXACT `Step`/`StepperProps` shape you recorded in Task 1 Step 3 (not the shape below, which is a placeholder illustrating the general structure — replace field names to match what you actually read from `stepper/index.d.ts`), plus the confirmed `Badge` import path:

```typescript
import { Badge } from "@datarobot/design-system/badge";
import { Stepper } from "@datarobot/design-system/stepper";
import { badgePropsForStatus } from "./status-mapping";
import type { PipelineState } from "./pipeline-types";

export interface PipelineViewProps {
  state: PipelineState;
}

const STAGE_LABELS: Record<string, string> = {
  scan: "Scan",
  transform: "Transform",
  validate: "Validate",
  deploy: "Deploy",
  receipt: "Receipt",
};

export function PipelineView({ state }: PipelineViewProps): JSX.Element {
  if (state.length === 0) {
    return <p>No pipeline activity yet.</p>;
  }

  // Replace this with the real Step[] shape from Task 1's verification --
  // this is illustrative, not verified.
  const steps = state.map((stage) => ({
    key: stage.id,
    label: STAGE_LABELS[stage.id] ?? stage.id,
  }));
  const activeKey = state.find((s) => s.status === "active")?.id ?? state[state.length - 1].id;

  return (
    <div>
      <Stepper steps={steps} activeKey={activeKey} isDisabled />
      <ul>
        {state.map((stage) => (
          <li key={stage.id}>
            <span>{STAGE_LABELS[stage.id] ?? stage.id}</span>
            <Badge {...badgePropsForStatus(stage.status)} />
            {stage.detail && <span>{stage.detail}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
```

Adjust `Stepper`'s props to match the real `StepperProps` interface you read in Task 1 (field names for each step, whether `activeKey` expects a string/index/other type, etc.) — this illustrative code is deliberately not final.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test`
Expected: 3 passed (adjust the test's text matchers if the real `Stepper` renders labels differently than assumed — the important thing is the 5 stage identifiers and the detail message are genuinely visible in the rendered output)

- [ ] **Step 5: Commit**

```bash
git add shell/companion/src/PipelineView.tsx shell/companion/src/PipelineView.test.tsx
git commit -m "feat: PipelineView component rendering stages via Stepper + Badge"
```

---

### Task 4: App entry point with i18next bootstrap and design-system CSS

**Files:**
- Modify: `shell/companion/src/main.tsx`
- Modify: `shell/companion/src/App.tsx` (or delete it and inline into main.tsx — your call, keep it simple)
- Test: `shell/companion/src/App.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `shell/companion/src/App.test.tsx`:

```typescript
import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("App", () => {
  it("renders without throwing and shows the empty-state message before any data arrives", () => {
    render(<App />);
    expect(screen.getByText(/no pipeline activity yet/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test`
Expected: FAIL (either module not found, or the default Vite starter's "count is 0" content instead of the expected empty-state message)

- [ ] **Step 3: Implement `App.tsx`**

Replace the contents of `shell/companion/src/App.tsx`:

```typescript
import { useEffect, useState } from "react";
import { PipelineView } from "./PipelineView";
import type { PipelineState } from "./pipeline-types";

export function App(): JSX.Element {
  const [state, setState] = useState<PipelineState>([]);

  useEffect(() => {
    const ws = new WebSocket(`ws://${window.location.host}/ws`);
    ws.onmessage = (event) => {
      setState(JSON.parse(event.data) as PipelineState);
    };
    return () => ws.close();
  }, []);

  return <PipelineView state={state} />;
}
```

- [ ] **Step 4: Implement `main.tsx`**

Replace the contents of `shell/companion/src/main.tsx`:

```typescript
import i18n from "i18next";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { I18nextProvider } from "react-i18next";
import { App } from "./App";
import "@datarobot/design-system/styles/index.min.css";

i18n.init({
  lng: "en",
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nextProvider i18n={i18n}>
      <App />
    </I18nextProvider>
  </StrictMode>,
);
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test`
Expected: 1 passed (plus the previously-passing tests from Tasks 2-3 still passing: run `npm run test` with no filter to confirm the whole suite, expect 8 passed total)

- [ ] **Step 6: Confirm the production build still works**

Run: `npm run build`
Expected: builds successfully, no TypeScript errors

- [ ] **Step 7: Commit**

```bash
git add shell/companion/src/App.tsx shell/companion/src/main.tsx shell/companion/src/App.test.tsx
git commit -m "feat: App entry point with i18next bootstrap, WebSocket state feed, design-system CSS"
```

---

### Task 5: `web-controller.ts` — Node HTTP + WebSocket server

**Files:**
- Create: `shell/extensions/superrobot/web-controller.ts`
- Test: `shell/extensions/superrobot/web-controller.test.ts`

This mirrors the existing `RailController` interface (`start(state)`/`update(state)`/`stop()`) from `shell/extensions/superrobot/rail-widget.ts`, but serves the companion app over HTTP and pushes state over a WebSocket instead of rendering ASCII.

- [ ] **Step 1: Add the `ws` dependency**

In `shell/package.json`, add to `"dependencies"`:
```json
"ws": "^8.18.0"
```
And to `"devDependencies"`:
```json
"@types/ws": "^8.5.0"
```
Run: `cd shell && npm install`

- [ ] **Step 2: Write the failing test**

Create `shell/extensions/superrobot/web-controller.test.ts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import WebSocket from "ws";
import { createWebController } from "./web-controller.ts";
import type { PipelineState } from "./pipeline-state.ts";

const SAMPLE_STATE: PipelineState = [
  { id: "scan", status: "done", detail: "ok" },
  { id: "transform", status: "pending", detail: "" },
  { id: "validate", status: "pending", detail: "" },
  { id: "deploy", status: "pending", detail: "" },
  { id: "receipt", status: "pending", detail: "" },
];

test("web controller serves state over a websocket and can be stopped", async () => {
  const controller = createWebController({ port: 0 });
  const { port } = controller.start(SAMPLE_STATE);
  assert.ok(port > 0);

  const received = await new Promise<PipelineState>((resolve, reject) => {
    const ws = new WebSocket(`ws://127.0.0.1:${port}/ws`);
    ws.on("message", (data) => {
      resolve(JSON.parse(data.toString()));
      ws.close();
    });
    ws.on("error", reject);
    ws.on("open", () => {
      controller.update(SAMPLE_STATE.map((s) => (s.id === "transform" ? { ...s, status: "active" } : s)));
    });
  });

  assert.equal(received.find((s) => s.id === "transform")?.status, "active");

  await controller.stop();
});

test("web controller does not throw when the requested port is already in use", async () => {
  const blocker = createWebController({ port: 0 });
  const { port } = blocker.start(SAMPLE_STATE);

  const second = createWebController({ port });
  assert.doesNotThrow(() => second.start(SAMPLE_STATE));
  await second.stop();
  await blocker.stop();
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd shell && node --test extensions/superrobot/web-controller.test.ts`
Expected: FAIL with a module-not-found error for `./web-controller.ts`

- [ ] **Step 4: Implement `web-controller.ts`**

```typescript
import { createServer, type Server } from "node:http";
import { readFile } from "node:fs/promises";
import { join, extname, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocketServer, type WebSocket } from "ws";
import type { PipelineState } from "./pipeline-state.ts";

// Use fileURLToPath + dirname rather than import.meta.dirname -- the
// latter needs Node 20.11+/21.2+, while this package's engines field
// only guarantees Node >=20.
const __dirname = dirname(fileURLToPath(import.meta.url));
const COMPANION_DIST_DIR = join(__dirname, "..", "..", "companion", "dist");

const CONTENT_TYPES: Record<string, string> = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
};

export interface WebController {
  /** Starts the server (idempotent-ish: safe to call once per controller
   * instance) and returns the port it actually bound to. Never throws --
   * a bind failure (e.g. port in use) is caught and logged, and the
   * pipeline tool call that triggered this must not fail because of it. */
  start(state: PipelineState): { port: number };
  update(state: PipelineState): void;
  stop(): Promise<void>;
}

export interface WebControllerOptions {
  /** Port to bind to. 0 asks the OS for any free port. */
  port?: number;
}

export function createWebController(options: WebControllerOptions = {}): WebController {
  let server: Server | undefined;
  let wss: WebSocketServer | undefined;
  let latestState: PipelineState = [];
  const clients = new Set<WebSocket>();

  function broadcast(state: PipelineState): void {
    const payload = JSON.stringify(state);
    for (const client of clients) {
      if (client.readyState === client.OPEN) {
        client.send(payload);
      }
    }
  }

  return {
    start(state: PipelineState): { port: number } {
      latestState = state;
      try {
        server = createServer((req, res) => {
          void (async () => {
            const urlPath = req.url === "/" ? "/index.html" : (req.url ?? "/index.html");
            const filePath = join(COMPANION_DIST_DIR, urlPath);
            try {
              const body = await readFile(filePath);
              const contentType = CONTENT_TYPES[extname(filePath)] ?? "application/octet-stream";
              res.writeHead(200, { "Content-Type": contentType });
              res.end(body);
            } catch {
              res.writeHead(404);
              res.end("Not found");
            }
          })();
        });

        wss = new WebSocketServer({ server, path: "/ws" });
        wss.on("connection", (ws) => {
          clients.add(ws);
          ws.send(JSON.stringify(latestState));
          ws.on("close", () => clients.delete(ws));
        });

        server.listen(options.port ?? 0);
        const address = server.address();
        const boundPort = typeof address === "object" && address ? address.port : 0;
        return { port: boundPort };
      } catch (err) {
        console.error("[superrobot] web companion failed to start:", err);
        return { port: 0 };
      }
    },

    update(state: PipelineState): void {
      latestState = state;
      broadcast(state);
    },

    async stop(): Promise<void> {
      for (const client of clients) {
        client.close();
      }
      clients.clear();
      await new Promise<void>((resolve) => (wss ? wss.close(() => resolve()) : resolve()));
      await new Promise<void>((resolve) => (server ? server.close(() => resolve()) : resolve()));
    },
  };
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd shell && node --test extensions/superrobot/web-controller.test.ts`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add shell/package.json shell/extensions/superrobot/web-controller.ts shell/extensions/superrobot/web-controller.test.ts
git commit -m "feat: web-controller.ts serving companion app + WebSocket state feed"
```

---

### Task 6: Wire `web-controller` into `tools.ts` alongside the existing rail widget

**Files:**
- Modify: `shell/extensions/superrobot/tools.ts`
- Test: `shell/extensions/superrobot/tools.test.ts` (or wherever the existing tool tests live — check the actual test file name/path first with `grep -rl "registerSuperRobotTools" shell/extensions/superrobot/*.test.ts`)

- [ ] **Step 1: Read the current `tools.ts` to find every `rc.start(pipeline)` / `rc.update(pipeline)` call site**

Run: `grep -n "rc\.\(start\|update\)" shell/extensions/superrobot/tools.ts`

You'll add a matching `web.start(pipeline)` / `web.update(pipeline)` call directly alongside each one found (not a refactor of the whole file — minimal, additive changes at each existing call site).

- [ ] **Step 2: Add the `webFor` helper alongside the existing `railFor` helper**

Find the existing `railFor(ctx)` function in `tools.ts` (per the codebase survey, it looks like:
```typescript
let rail: RailController | undefined;
function railFor(ctx: ExtensionContext): RailController {
  if (!rail) rail = createRailController(ctx);
  return rail;
}
```
) and add an equivalent:

```typescript
import { createWebController, type WebController } from "./web-controller.ts";

let web: WebController | undefined;
function webFor(): WebController {
  if (!web) web = createWebController({ port: 0 });
  return web;
}
```

- [ ] **Step 3: Add a matching `web.start(pipeline)` / `web.update(pipeline)` call next to every existing `rc.start(pipeline)` / `rc.update(pipeline)` call site found in Step 1**

For example, wherever you see:
```typescript
const rc = railFor(ctx);
...
rc.start(pipeline);
```
add immediately after:
```typescript
const wc = webFor();
...
wc.start(pipeline);
```
And wherever you see `rc.update(pipeline)`, add `wc.update(pipeline)` right after it. Do this for all 5 pipeline-mutating tool handlers (scan, transform, validate, deploy, receipts) — not `memory_ensure`, which per the existing code doesn't touch pipeline state.

- [ ] **Step 4: Confirm the existing tool test suite still passes**

Run: `cd shell && npm run typecheck && npm run test` (check `shell/package.json`'s actual script names first — use whatever the real typecheck/test scripts are called)
Expected: all existing tests pass unchanged — this task only adds new calls alongside existing ones, it doesn't change any existing behavior or return values.

- [ ] **Step 5: Add one integration test proving a web-server bind failure doesn't fail the tool call**

Add to the existing tools test file (or a new one if none exists yet for `tools.ts` — check first) a test that mocks/forces `createWebController` to throw or return `{ port: 0 }`, and asserts that `superrobot_scan`'s tool call still completes successfully and returns its normal result (i.e., a companion UI failure is silently non-fatal to the actual pipeline operation, matching the spec's explicit requirement).

- [ ] **Step 6: Commit**

```bash
git add shell/extensions/superrobot/tools.ts shell/extensions/superrobot/*.test.ts
git commit -m "feat: notify the web companion alongside the terminal rail widget"
```

---

### Task 7: Verification pass

- [ ] **Step 1: Build the companion app for real**

Run: `cd shell/companion && npm run build`
Expected: succeeds, produces `shell/companion/dist/`

- [ ] **Step 2: Run the full companion test suite**

Run: `cd shell/companion && npm run test`
Expected: all tests pass (from Tasks 2-4)

- [ ] **Step 3: Run the full shell test suite**

Run: `cd shell && npm run test` (or the equivalent real script name — verify first)
Expected: all tests pass, including the new `web-controller.test.ts` and the updated tool tests

- [ ] **Step 4: Typecheck both packages**

Run: `cd shell && npm run typecheck`
Run: `cd shell/companion && npx tsc --noEmit`
Expected: no errors in either

- [ ] **Step 5: Confirm `.gitignore` covers both new build/dependency directories**

Check the repo root `.gitignore` includes `shell/companion/node_modules/` and `shell/companion/dist/` (add them if missing).

- [ ] **Step 6: Commit any fixes**

```bash
git add -A
git commit -m "fix: address issues found in Phase 2 verification pass"
```

---

## Not in this plan (deferred)

- Automatically opening a browser tab when the companion starts (the plan only requires `ctx.ui.notify()` announcing the URL — auto-open via `pi.exec("open", [url])` or `xdg-open` is a nice-to-have, not required here).
- Supporting more than one concurrent companion server instance per shell process (single global controller, matching the existing single global `RailController` pattern).
- Any change to the terminal rail-widget itself — it remains the unconditional default.
