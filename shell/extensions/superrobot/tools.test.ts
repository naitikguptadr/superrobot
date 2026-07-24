import { test } from "node:test";
import assert from "node:assert/strict";
import type { ExtensionAPI, ExtensionContext } from "@mariozechner/pi-coding-agent";
import { registerSuperRobotTools } from "./tools.ts";
import type { WebController } from "./web-controller.ts";
import type { PipelineState } from "./pipeline-state.ts";

type ExecResult = { stdout: string; stderr: string; code: number };
type ToolExecute = (
  toolCallId: string,
  params: unknown,
  signal: AbortSignal | undefined,
  onUpdate: unknown,
  ctx: ExtensionContext,
) => Promise<{ content: Array<{ type: string; text: string }>; details: unknown }>;

/** Captures every tool registered via pi.registerTool() and fakes pi.exec() with a caller-supplied impl. */
function fakePi(execImpl: (args: string[]) => ExecResult): {
  pi: ExtensionAPI;
  tools: Map<string, ToolExecute>;
} {
  const tools = new Map<string, ToolExecute>();
  const pi = {
    registerTool(tool: { name: string; execute: ToolExecute }) {
      tools.set(tool.name, tool.execute);
    },
    exec: async (_command: string, args: string[]) => execImpl(args),
  } as unknown as ExtensionAPI;
  return { pi, tools };
}

function fakeCtx(): ExtensionContext {
  return {
    ui: {
      // Throwing here (rather than a no-op) mirrors rail-widget.test.ts's own
      // "ctx invalidated" fixture: createRailController's draw() catches this,
      // marks itself stopped, and -- crucially -- never arms its 90ms
      // setInterval. A no-op setWidget would let that interval start ticking
      // for real and keep the test process alive forever, since nothing in
      // these tool handlers ever calls rail.stop().
      setWidget: () => {
        throw new Error("no real widget host in tests");
      },
      confirm: async () => true,
      notify: () => {},
    },
  } as unknown as ExtensionContext;
}

function scanExecOk(): (args: string[]) => ExecResult {
  return () => ({
    stdout: JSON.stringify({ detected_framework: "langchain", confidence: 0.9, env_vars: ["A"] }),
    stderr: "",
    code: 0,
  });
}

test("superrobot_scan notifies both the rail widget and the web companion", async () => {
  const { pi, tools } = fakePi(scanExecOk());
  const calls: string[] = [];
  const fakeWebController: WebController = {
    start: async (state: PipelineState) => {
      calls.push(`start:${state.find((s) => s.id === "scan")?.status}`);
      return { port: 4321 };
    },
    update: (state: PipelineState) => {
      calls.push(`update:${state.find((s) => s.id === "scan")?.status}`);
    },
    stop: async () => {},
  };
  registerSuperRobotTools(pi, () => fakeWebController);

  const scan = tools.get("superrobot_scan");
  assert.ok(scan, "superrobot_scan should be registered");
  const result = await scan!("id1", { path: "tests/fixtures/langchain_agent" }, undefined, undefined, fakeCtx());

  assert.equal(result.content[0]?.text, "langchain detected, 1 env vars, conf 0.90");
  assert.deepEqual(calls, ["start:active", "update:done"]);
});

test("a web companion factory that throws synchronously does not fail superrobot_scan", async () => {
  const { pi, tools } = fakePi(scanExecOk());
  registerSuperRobotTools(pi, () => {
    throw new Error("boom: cannot construct web controller");
  });

  const scan = tools.get("superrobot_scan");
  assert.ok(scan, "superrobot_scan should be registered");
  const result = await scan!("id1", { path: "tests/fixtures/langchain_agent" }, undefined, undefined, fakeCtx());

  assert.equal(result.content[0]?.text, "langchain detected, 1 env vars, conf 0.90");
});

test("a web companion whose start()/update() reject or throw does not fail superrobot_scan", async () => {
  const { pi, tools } = fakePi(scanExecOk());
  const fakeWebController: WebController = {
    start: async () => {
      throw new Error("bind failed");
    },
    update: () => {
      throw new Error("broadcast failed");
    },
    stop: async () => {},
  };
  registerSuperRobotTools(pi, () => fakeWebController);

  const scan = tools.get("superrobot_scan");
  const result = await scan!("id1", { path: "tests/fixtures/langchain_agent" }, undefined, undefined, fakeCtx());

  assert.equal(result.content[0]?.text, "langchain detected, 1 env vars, conf 0.90");
});

test("superrobot_scan's own failure path is unaffected by a broken web companion", async () => {
  const { pi, tools } = fakePi(() => ({ stdout: "not json", stderr: "bad output", code: 1 }));
  registerSuperRobotTools(pi, () => {
    throw new Error("boom: cannot construct web controller");
  });

  const scan = tools.get("superrobot_scan");
  await assert.rejects(
    () => scan!("id1", { path: "x" }, undefined, undefined, fakeCtx()),
    /superrobot scan failed/,
  );
});
