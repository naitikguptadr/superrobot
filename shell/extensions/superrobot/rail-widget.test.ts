import { test } from "node:test";
import assert from "node:assert/strict";
import { visibleWidth } from "@mariozechner/pi-tui";
import { freshPipeline, withStageActive, withStageDone } from "./pipeline-state.ts";
import { renderRailLines } from "./rail-widget.ts";

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
