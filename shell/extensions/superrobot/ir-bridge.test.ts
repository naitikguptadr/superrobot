import { test } from "node:test";
import assert from "node:assert/strict";
import type { ExtensionAPI, ExtensionContext } from "@mariozechner/pi-coding-agent";
import type { ExecFn } from "./cli-bridge.ts";
import { createIrBridge, registerIrTools, resolvePythonExecutable } from "./ir-bridge.ts";

type ExecResult = { stdout: string; stderr: string; code: number };
type ToolExecute = (
  toolCallId: string,
  params: unknown,
  signal: AbortSignal | undefined,
  onUpdate: unknown,
  ctx: ExtensionContext,
) => Promise<{ content: Array<{ type: string; text: string }>; details: unknown }>;

function fakeExec(impl: (args: string[]) => ExecResult): ExecFn {
  return async (args: string[]) => impl(args);
}

/**
 * Captures every tool registered via pi.registerTool() and fakes pi.exec().
 * `execImpl` receives the full argv (interpreter args included) so tests can
 * assert on the `-m superrobot.ir` prefix as well as the subcommand.
 */
function fakePi(execImpl: (command: string, args: string[]) => ExecResult): {
  pi: ExtensionAPI;
  tools: Map<string, ToolExecute>;
  registered: Map<string, { promptGuidelines?: string[]; executionMode?: string }>;
} {
  const tools = new Map<string, ToolExecute>();
  const registered = new Map<string, { promptGuidelines?: string[]; executionMode?: string }>();
  const pi = {
    registerTool(tool: {
      name: string;
      execute: ToolExecute;
      promptGuidelines?: string[];
      executionMode?: string;
    }) {
      tools.set(tool.name, tool.execute);
      registered.set(tool.name, {
        promptGuidelines: tool.promptGuidelines,
        executionMode: tool.executionMode,
      });
    },
    on() {},
    exec: async (command: string, args: string[]) => execImpl(command, args),
  } as unknown as ExtensionAPI;
  return { pi, tools, registered };
}

function fakeCtx(notifyCalls?: string[]): ExtensionContext {
  return {
    ui: {
      notify: (message: string) => {
        notifyCalls?.push(message);
      },
      confirm: async () => true,
    },
  } as unknown as ExtensionContext;
}

const CLEAN_EXTRACT = {
  ir: { entry_points: [{ module: "app", function: "main" }] },
  coverage: { clean: true, blocking: [], unaccounted: [], report: "all facts migrated" },
  targetFramework: "langgraph",
};

// --- bridge-level ---------------------------------------------------------

test("extract: builds args and parses JSON on success", async () => {
  let capturedArgs: string[] = [];
  const ir = createIrBridge(
    fakeExec((args) => {
      capturedArgs = args;
      return { stdout: JSON.stringify(CLEAN_EXTRACT), stderr: "", code: 0 };
    }),
  );
  const result = await ir.extract("/repo");
  assert.deepEqual(capturedArgs, ["extract", "/repo"]);
  assert.equal(result.ok, true);
  if (result.ok) assert.equal(result.data.targetFramework, "langgraph");
});

test("extract/report/spec pass --decisions through; scaffold passes --framework/--llm-model", async () => {
  const seen: string[][] = [];
  const ir = createIrBridge(
    fakeExec((args) => {
      seen.push(args);
      return { stdout: "{}", stderr: "", code: 0 };
    }),
  );
  await ir.extract("/repo", { decisions: "d.yaml" });
  await ir.report("/repo", { decisions: "d.yaml" });
  await ir.decisionsTemplate("/repo");
  await ir.spec("/repo", { decisions: "d.yaml" });
  await ir.scaffold("/out", { framework: "langgraph", llmModel: "azure/gpt-5" });
  assert.deepEqual(seen, [
    ["extract", "/repo", "--decisions", "d.yaml"],
    ["report", "/repo", "--decisions", "d.yaml"],
    ["decisions-template", "/repo"],
    ["spec", "/repo", "--decisions", "d.yaml"],
    ["scaffold", "/out", "--framework", "langgraph", "--llm-model", "azure/gpt-5"],
  ]);
});

test("a non-zero exit carrying the JSON error shape becomes a structured ir_error, not a parse failure", async () => {
  const ir = createIrBridge(
    fakeExec(() => ({
      stdout: JSON.stringify({ error: "no python files under /repo", kind: "EmptyRepoError" }),
      stderr: "traceback noise on stderr\n",
      code: 1,
    })),
  );
  const result = await ir.extract("/repo");
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.reason, "ir_error");
    assert.equal(result.kind, "EmptyRepoError");
    // The message must be the JSON `error` field, not the stderr tail and not
    // a "did not return JSON" parse complaint (audit C21).
    assert.equal(result.message, "no python files under /repo");
    assert.doesNotMatch(result.message, /did not return JSON/);
  }
});

test("genuinely non-JSON stdout is still reported as parse_error", async () => {
  const ir = createIrBridge(fakeExec(() => ({ stdout: "Traceback...", stderr: "", code: 1 })));
  const result = await ir.extract("/repo");
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.reason, "parse_error");
});

test("a missing interpreter is reported as not_found", async () => {
  const ir = createIrBridge(async () => {
    throw new Error("spawn python3 ENOENT");
  });
  const result = await ir.extract("/repo");
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.reason, "not_found");
});

test("resolvePythonExecutable prefers SUPERROBOT_PYTHON, then VIRTUAL_ENV", () => {
  assert.equal(resolvePythonExecutable({ SUPERROBOT_PYTHON: "/custom/python" }), "/custom/python");
  assert.equal(resolvePythonExecutable({ VIRTUAL_ENV: "/venv" }), "/venv/bin/python");
});

// --- tool-level -----------------------------------------------------------

function registerWith(execImpl: (command: string, args: string[]) => ExecResult) {
  const harness = fakePi(execImpl);
  registerIrTools(harness.pi);
  return harness;
}

test("all five IR tools are registered, sequential, with prompt guidelines", () => {
  const { tools, registered } = registerWith(() => ({ stdout: "{}", stderr: "", code: 0 }));
  for (const name of ["sr_extract", "sr_report", "sr_decisions", "sr_spec", "sr_scaffold"]) {
    assert.ok(tools.get(name), `${name} should be registered`);
    const meta = registered.get(name);
    assert.equal(meta?.executionMode, "sequential", `${name} should run sequentially`);
    assert.ok((meta?.promptGuidelines ?? []).length > 0, `${name} should carry promptGuidelines`);
  }
  // The whole architecture depends on the agent not routing around a refusal.
  const specGuidelines = (registered.get("sr_spec")?.promptGuidelines ?? []).join(" ");
  assert.match(specGuidelines, /sr_decisions/);
});

test("tools invoke the resolved interpreter with -m superrobot.ir", async () => {
  let capturedCommand = "";
  let capturedArgs: string[] = [];
  const { tools } = registerWith((command, args) => {
    capturedCommand = command;
    capturedArgs = args;
    return { stdout: JSON.stringify(CLEAN_EXTRACT), stderr: "", code: 0 };
  });
  await tools.get("sr_extract")!("id", { repo: "/repo" }, undefined, undefined, fakeCtx());
  assert.ok(capturedCommand.length > 0, "an interpreter should be resolved, never empty");
  assert.deepEqual(capturedArgs.slice(0, 2), ["-m", "superrobot.ir"]);
  assert.deepEqual(capturedArgs.slice(2), ["extract", "/repo"]);
});

test("sr_extract parses a successful response and reports the coverage verdict", async () => {
  const { tools } = registerWith(() => ({ stdout: JSON.stringify(CLEAN_EXTRACT), stderr: "", code: 0 }));
  const result = await tools.get("sr_extract")!("id", { repo: "/repo" }, undefined, undefined, fakeCtx());
  assert.match(result.content[0]!.text, /clean/);
  assert.equal((result.details as { targetFramework: string }).targetFramework, "langgraph");
});

test("sr_extract surfaces blocking and unaccounted counts when the ledger is dirty", async () => {
  const { tools } = registerWith(() => ({
    stdout: JSON.stringify({
      ir: {},
      coverage: {
        clean: false,
        blocking: [{ fact: "llm_call@app.py:12", reason: "unknown provider" }],
        unaccounted: ["tool@t.py:3"],
        report: "1 blocking",
      },
      targetFramework: "langgraph",
    }),
    stderr: "",
    code: 0,
  }));
  const notifies: string[] = [];
  const result = await tools.get("sr_extract")!("id", { repo: "/repo" }, undefined, undefined, fakeCtx(notifies));
  assert.match(result.content[0]!.text, /1 blocking/);
  assert.match(result.content[0]!.text, /1 unaccounted/);
  assert.match(result.content[0]!.text, /sr_decisions/);
  assert.equal(notifies.length, 1);
});

test("sr_report parses a successful response and returns the ledger text verbatim", async () => {
  const { tools } = registerWith(() => ({
    stdout: JSON.stringify({ report: "MIGRATED: 4\nDEFERRED: 1\nBLOCKING: 0", clean: true }),
    stderr: "",
    code: 0,
  }));
  const result = await tools.get("sr_report")!("id", { repo: "/repo" }, undefined, undefined, fakeCtx());
  assert.match(result.content[0]!.text, /MIGRATED: 4/);
  assert.equal((result.details as { clean: boolean }).clean, true);
});

test("sr_decisions parses a successful response and returns the template yaml", async () => {
  const { tools } = registerWith(() => ({
    stdout: JSON.stringify({ yaml: "decisions:\n  - fact: x\n", path: "/repo/decisions.yaml", blockingCount: 1 }),
    stderr: "",
    code: 0,
  }));
  const result = await tools.get("sr_decisions")!("id", { repo: "/repo" }, undefined, undefined, fakeCtx());
  assert.match(result.content[0]!.text, /decisions:/);
  assert.match(result.content[0]!.text, /\/repo\/decisions\.yaml/);
  assert.equal((result.details as { blockingCount: number }).blockingCount, 1);
});

test("sr_spec parses a successful response and returns the agent spec", async () => {
  const { tools } = registerWith(() => ({
    stdout: JSON.stringify({ agentSpec: "model: azure/gpt-5\ntools: []\n" }),
    stderr: "",
    code: 0,
  }));
  const result = await tools.get("sr_spec")!("id", { repo: "/repo" }, undefined, undefined, fakeCtx());
  assert.match(result.content[0]!.text, /model: azure\/gpt-5/);
});

test("sr_scaffold parses a successful response and lists the DataRobot steps", async () => {
  const { tools } = registerWith(() => ({
    stdout: JSON.stringify({
      targetDir: "/out",
      framework: "langgraph",
      steps: [
        { script: "clone_template.py", ok: true },
        { script: "select_framework.py", ok: true },
      ],
    }),
    stderr: "",
    code: 0,
  }));
  const result = await tools.get("sr_scaffold")!(
    "id",
    { targetDir: "/out", framework: "langgraph", llmModel: "azure/gpt-5" },
    undefined,
    undefined,
    fakeCtx(),
  );
  assert.match(result.content[0]!.text, /clone_template\.py/);
  assert.equal((result.details as { framework: string }).framework, "langgraph");
});

test("a non-zero exit surfaces as a structured tool error, never as a JSON parse failure", async () => {
  const { tools } = registerWith(() => ({
    stdout: JSON.stringify({ error: "/repo is not a directory", kind: "RepoNotFoundError" }),
    stderr: "irrelevant traceback\n",
    code: 2,
  }));
  await assert.rejects(
    () => tools.get("sr_extract")!("id", { repo: "/repo" }, undefined, undefined, fakeCtx()),
    (err: Error) => {
      assert.match(err.message, /RepoNotFoundError/);
      assert.match(err.message, /\/repo is not a directory/);
      assert.doesNotMatch(err.message, /did not return JSON/);
      return true;
    },
  );
});

test("truly malformed stdout still fails loudly, tagged as a parse error", async () => {
  const { tools } = registerWith(() => ({ stdout: "<html>500</html>", stderr: "", code: 1 }));
  await assert.rejects(
    () => tools.get("sr_report")!("id", { repo: "/repo" }, undefined, undefined, fakeCtx()),
    /did not return JSON/,
  );
});

test("sr_spec refusing on an unclean ledger surfaces the blocking reasons, not an empty result", async () => {
  const { tools } = registerWith(() => ({
    stdout: JSON.stringify({
      error: "coverage ledger is not clean",
      kind: "LedgerNotCleanError",
      blocking: [
        { fact: "llm_call@agents/planner.py:41", reason: "unknown provider 'acme'; no DataRobot equivalent" },
        { fact: "state@memory.py:9", reason: "redis-backed state has no target representation" },
      ],
    }),
    stderr: "",
    code: 1,
  }));
  const notifies: string[] = [];
  const result = await tools.get("sr_spec")!("id", { repo: "/repo" }, undefined, undefined, fakeCtx(notifies));
  const text = result.content[0]!.text;
  assert.match(text, /agents\/planner\.py:41/);
  assert.match(text, /unknown provider 'acme'/);
  assert.match(text, /memory\.py:9/);
  assert.match(text, /sr_decisions/);
  assert.match(text, /human/i);
  const details = result.details as { refused: boolean; kind: string; blocking: unknown[] };
  assert.equal(details.refused, true);
  assert.equal(details.kind, "LedgerNotCleanError");
  assert.equal(details.blocking.length, 2);
  assert.equal(notifies.length, 1);
});

test("sr_spec refusal is a refusal, not a crash — the tool resolves rather than throwing", async () => {
  const { tools } = registerWith(() => ({
    stdout: JSON.stringify({ error: "coverage ledger is not clean", kind: "LedgerNotCleanError", blocking: [] }),
    stderr: "",
    code: 1,
  }));
  await assert.doesNotReject(() =>
    tools.get("sr_spec")!("id", { repo: "/repo" }, undefined, undefined, fakeCtx()),
  );
});

test("sr_spec failing for a non-ledger reason is a real error and does throw", async () => {
  const { tools } = registerWith(() => ({
    stdout: JSON.stringify({ error: "no IR on disk; run sr_extract first", kind: "MissingIrError" }),
    stderr: "",
    code: 1,
  }));
  await assert.rejects(
    () => tools.get("sr_spec")!("id", { repo: "/repo" }, undefined, undefined, fakeCtx()),
    /MissingIrError/,
  );
});
