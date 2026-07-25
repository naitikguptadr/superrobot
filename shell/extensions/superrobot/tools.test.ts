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
type EventHandler = (event: unknown, ctx?: ExtensionContext) => unknown;

/** Captures every tool registered via pi.registerTool() and fakes pi.exec() with a caller-supplied impl. */
function fakePi(execImpl: (args: string[]) => ExecResult): {
  pi: ExtensionAPI;
  tools: Map<string, ToolExecute>;
  handlers: Map<string, EventHandler>;
} {
  const tools = new Map<string, ToolExecute>();
  const handlers = new Map<string, EventHandler>();
  const pi = {
    registerTool(tool: { name: string; execute: ToolExecute }) {
      tools.set(tool.name, tool.execute);
    },
    on(event: string, handler: EventHandler) {
      handlers.set(event, handler);
    },
    exec: async (_command: string, args: string[]) => execImpl(args),
  } as unknown as ExtensionAPI;
  return { pi, tools, handlers };
}

function fakeCtx(notifyCalls?: string[]): ExtensionContext {
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
      notify: (message: string) => {
        notifyCalls?.push(message);
      },
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

test("the web companion URL is only notified once per session, even across multiple pipeline tool calls", async () => {
  const { pi, tools } = fakePi((args) => {
    if (args[0] === "transform") {
      return { stdout: JSON.stringify({ files: ["a.py"] }), stderr: "", code: 0 };
    }
    return scanExecOk()(args);
  });
  const fakeWebController: WebController = {
    start: async () => ({ port: 4321 }),
    update: () => {},
    stop: async () => {},
  };
  registerSuperRobotTools(pi, () => fakeWebController);

  const scan = tools.get("superrobot_scan");
  const transform = tools.get("superrobot_transform");
  assert.ok(scan && transform, "both tools should be registered");

  const notifyCalls: string[] = [];
  await scan!("id1", { path: "tests/fixtures/langchain_agent" }, undefined, undefined, fakeCtx(notifyCalls));
  await scan!("id2", { path: "tests/fixtures/langchain_agent" }, undefined, undefined, fakeCtx(notifyCalls));
  await transform!("id3", { path: "tests/fixtures/langchain_agent", outputDir: "out" }, undefined, undefined, fakeCtx(notifyCalls));

  assert.equal(notifyCalls.length, 1, "the companion URL should only be notified once");
  assert.match(notifyCalls[0]!, /^SuperRobot companion UI: http:\/\/localhost:4321$/);
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

test("session_shutdown does not throw when no pipeline tool has run yet (no controllers created)", async () => {
  const { pi, handlers } = fakePi(scanExecOk());
  registerSuperRobotTools(pi, () => {
    throw new Error("web controller should never be constructed in this test");
  });

  const shutdown = handlers.get("session_shutdown");
  assert.ok(shutdown, "session_shutdown handler should be registered");
  await assert.doesNotReject(() => Promise.resolve(shutdown!({ type: "session_shutdown", reason: "quit" })));
});

test("session_shutdown stops both the rail and web controllers once they've been created", async () => {
  const { pi, tools, handlers } = fakePi(scanExecOk());
  const webStopCalls: string[] = [];
  const fakeWebController: WebController = {
    start: async () => ({ port: 4321 }),
    update: () => {},
    stop: async () => {
      webStopCalls.push("stopped");
    },
  };
  registerSuperRobotTools(pi, () => fakeWebController);

  // A ctx whose setWidget records calls instead of throwing, so the pipeline
  // tool arms the rail's real setInterval -- and so we can observe
  // RailController.stop()'s call to ctx.ui.setWidget(key, undefined), which
  // is otherwise not externally observable since RailController exposes no
  // "was stop() called" flag of its own.
  const setWidgetCalls: Array<[string, unknown]> = [];
  const ctx = {
    ui: {
      setWidget: (key: string, value: unknown) => {
        setWidgetCalls.push([key, value]);
      },
      confirm: async () => true,
      notify: () => {},
    },
  } as unknown as ExtensionContext;

  const scan = tools.get("superrobot_scan");
  assert.ok(scan, "superrobot_scan should be registered");
  await scan!("id1", { path: "tests/fixtures/langchain_agent" }, undefined, undefined, ctx);

  const shutdown = handlers.get("session_shutdown");
  assert.ok(shutdown, "session_shutdown handler should be registered");
  await shutdown!({ type: "session_shutdown", reason: "quit" });

  assert.deepEqual(webStopCalls, ["stopped"], "WebController.stop() should be awaited");
  const lastSetWidgetCall = setWidgetCalls.at(-1);
  assert.equal(lastSetWidgetCall?.[1], undefined, "RailController.stop() should clear the widget");
});

test("session_shutdown does not throw when the web companion's stop() rejects", async () => {
  const { pi, tools, handlers } = fakePi(scanExecOk());
  const fakeWebController: WebController = {
    start: async () => ({ port: 4321 }),
    update: () => {},
    stop: async () => {
      throw new Error("boom: stop() failed");
    },
  };
  registerSuperRobotTools(pi, () => fakeWebController);

  const scan = tools.get("superrobot_scan");
  const shutdown = handlers.get("session_shutdown");
  assert.ok(scan && shutdown, "superrobot_scan and session_shutdown should be registered");

  await scan!("id1", { path: "tests/fixtures/langchain_agent" }, undefined, undefined, fakeCtx());
  await assert.doesNotReject(() => Promise.resolve(shutdown!({ type: "session_shutdown", reason: "quit" })));
});

test("session_shutdown resets pipeline state so a later session doesn't inherit stale stage statuses", async () => {
  const { pi, tools, handlers } = fakePi((args) => {
    if (args[0] === "transform") {
      return { stdout: JSON.stringify({ files: ["a.py"] }), stderr: "", code: 0 };
    }
    return scanExecOk()(args);
  });
  const updateCalls: PipelineState[] = [];
  const fakeWebController: WebController = {
    start: async () => ({ port: 4321 }),
    update: (state: PipelineState) => {
      updateCalls.push(state);
    },
    stop: async () => {},
  };
  registerSuperRobotTools(pi, () => fakeWebController);

  const scan = tools.get("superrobot_scan");
  const transform = tools.get("superrobot_transform");
  const shutdown = handlers.get("session_shutdown");
  assert.ok(scan && transform && shutdown, "scan, transform, and session_shutdown should all be registered");

  // Finish a scan (marks the "scan" stage "done"), then end the session.
  await scan!("id1", { path: "tests/fixtures/langchain_agent" }, undefined, undefined, fakeCtx());
  await shutdown!({ type: "session_shutdown", reason: "quit" });

  // A brand new session's first pipeline call is transform, not scan -- if
  // `pipeline` wasn't reset by session_shutdown, the stale "scan: done" from
  // the previous session would still be sitting there.
  updateCalls.length = 0;
  await transform!("id2", { path: "tests/fixtures/langchain_agent", outputDir: "out" }, undefined, undefined, fakeCtx());

  const scanStage = updateCalls[0]?.find((s) => s.id === "scan");
  assert.equal(
    scanStage?.status,
    "pending",
    "pipeline should have been reset to fresh on session_shutdown, not carry over the prior session's 'done' scan stage",
  );
});

test("superrobot_transform arms the rail spinner even without a prior scan in the same session", async () => {
  const { pi, tools, handlers } = fakePi((args) => {
    if (args[0] === "transform") {
      return { stdout: JSON.stringify({ files: ["a.py"] }), stderr: "", code: 0 };
    }
    return scanExecOk()(args);
  });
  const fakeWebController: WebController = {
    start: async () => ({ port: 4321 }),
    update: () => {},
    stop: async () => {},
  };
  registerSuperRobotTools(pi, () => fakeWebController);

  const setWidgetCalls: Array<[string, unknown]> = [];
  const ctx = {
    ui: {
      setWidget: (key: string, value: unknown) => {
        setWidgetCalls.push([key, value]);
      },
      confirm: async () => true,
      notify: () => {},
    },
  } as unknown as ExtensionContext;

  const transform = tools.get("superrobot_transform");
  const shutdown = handlers.get("session_shutdown");
  assert.ok(transform && shutdown, "superrobot_transform and session_shutdown should be registered");

  try {
    // No superrobot_scan call anywhere before this -- transform is the very
    // first pipeline tool call in this session.
    await transform!("id1", { path: "tests/fixtures/langchain_agent", outputDir: "out" }, undefined, undefined, ctx);

    const callsRightAfterExecute = setWidgetCalls.length;
    // RailController.start() (unlike .update()) arms a ~90ms setInterval that
    // keeps redrawing so the spinner glyph animates. Give it a couple of
    // ticks: if only .update() was ever called (the bug), no interval is
    // armed and setWidget is never called again on its own.
    await new Promise((resolve) => setTimeout(resolve, 250));
    assert.ok(
      setWidgetCalls.length > callsRightAfterExecute,
      "the rail's spinner interval should have ticked after superrobot_transform, proving rc.start() (not just rc.update()) ran",
    );
  } finally {
    // Stop the rail's interval so it doesn't keep the test process alive.
    await shutdown!({ type: "session_shutdown", reason: "quit" });
  }
});

test("superrobot_receipts(action=operations) as the session's first pipeline call still arms the rail spinner for later calls", async () => {
  const { pi, tools, handlers } = fakePi((args) => {
    if (args[0] === "transform") {
      return { stdout: JSON.stringify({ files: ["a.py"] }), stderr: "", code: 0 };
    }
    return scanExecOk()(args);
  });
  const fakeWebController: WebController = {
    start: async () => ({ port: 4321 }),
    update: () => {},
    stop: async () => {},
  };
  registerSuperRobotTools(pi, () => fakeWebController);

  const setWidgetCalls: Array<[string, unknown]> = [];
  const ctx = {
    ui: {
      setWidget: (key: string, value: unknown) => {
        setWidgetCalls.push([key, value]);
      },
      confirm: async () => true,
      notify: () => {},
    },
  } as unknown as ExtensionContext;

  const receipts = tools.get("superrobot_receipts");
  const transform = tools.get("superrobot_transform");
  const shutdown = handlers.get("session_shutdown");
  assert.ok(receipts && transform && shutdown, "superrobot_receipts, superrobot_transform, and session_shutdown should be registered");

  try {
    // superrobot_receipts(action=operations) is the very first pipeline tool
    // call of the session -- it creates the `rail` singleton (via
    // railFor(ctx)) but, unlike show/replace, doesn't represent a pipeline
    // stage transition. If it fails to also call rc.start(), the rail's
    // spinner interval is never armed, and every later call (transform here)
    // sees isNewRail=false and only ever calls rc.update(), so the spinner
    // stays frozen for the rest of the session.
    await receipts!("id1", { action: "operations" }, undefined, undefined, ctx);
    await transform!("id2", { path: "tests/fixtures/langchain_agent", outputDir: "out" }, undefined, undefined, ctx);

    const callsRightAfterExecute = setWidgetCalls.length;
    // RailController.start() (unlike .update()) arms a ~90ms setInterval that
    // keeps redrawing so the spinner glyph animates. Give it a couple of
    // ticks: if only .update() was ever called (the bug), no interval is
    // armed and setWidget is never called again on its own.
    await new Promise((resolve) => setTimeout(resolve, 250));
    assert.ok(
      setWidgetCalls.length > callsRightAfterExecute,
      "the rail's spinner interval should have ticked after receipts(operations) + transform, proving rc.start() ran somewhere in that sequence",
    );
  } finally {
    // Stop the rail's interval so it doesn't keep the test process alive.
    await shutdown!({ type: "session_shutdown", reason: "quit" });
  }
});
