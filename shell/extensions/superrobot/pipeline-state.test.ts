import { test } from "node:test";
import assert from "node:assert/strict";
import { freshPipeline, withStageActive, withStageDone, withStageFailed } from "./pipeline-state.ts";

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
