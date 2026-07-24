import { test } from "node:test";
import assert from "node:assert/strict";
import { visibleWidth } from "@mariozechner/pi-tui";
import type { ExtensionContext } from "@mariozechner/pi-coding-agent";
import { freshPipeline, withStageActive, withStageDone, withStageFailed } from "./pipeline-state.ts";
import { createRailController, renderRailLines } from "./rail-widget.ts";

function fakeCtx(setWidget: (key: string, content: string[] | undefined) => void): ExtensionContext {
  return { ui: { setWidget } } as unknown as ExtensionContext;
}

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

test("a failed stage renders a red cross glyph and keeps its detail", () => {
  let state = freshPipeline();
  state = withStageFailed(state, "validate", "2 blocking findings");
  const lines = renderRailLines(state, 0).join("\n");
  assert.ok(lines.includes("✗"), "expected a cross glyph for a failed stage");
  assert.ok(lines.includes("2 blocking findings"));
});

test("createRailController: stop() clears the widget and further update() calls are a no-op", () => {
  const draws: Array<string[] | undefined> = [];
  const controller = createRailController(fakeCtx((_key, content) => draws.push(content)));

  controller.start(freshPipeline());
  assert.equal(draws.length, 1, "start() should draw once immediately");

  controller.stop();
  assert.equal(draws.at(-1), undefined, "stop() should clear the widget");

  const drawsAfterStop = draws.length;
  controller.update(freshPipeline());
  assert.equal(draws.length, drawsAfterStop, "update() after stop() must be a no-op, not resurrect the widget");
});

test("createRailController: a throwing ctx.ui.setWidget (simulating an invalidated context) is swallowed, not thrown", () => {
  const controller = createRailController(
    fakeCtx(() => {
      throw new Error("ctx invalidated");
    }),
  );
  assert.doesNotThrow(() => controller.start(freshPipeline()));
  assert.doesNotThrow(() => controller.update(freshPipeline()));
  assert.doesNotThrow(() => controller.stop());
});

test("createRailController: stop() called first (before any draw ever ran) swallows a throwing ctx too", () => {
  const controller = createRailController(
    fakeCtx(() => {
      throw new Error("ctx invalidated");
    }),
  );
  // Exercises stop()'s own try/catch directly -- nothing has set `stopped`
  // yet, so this hits stop()'s catch branch specifically, not start()'s.
  assert.doesNotThrow(() => controller.stop());
});
