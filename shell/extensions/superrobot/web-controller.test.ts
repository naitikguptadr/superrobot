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
  const { port } = await controller.start(SAMPLE_STATE);
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
  const { port } = await blocker.start(SAMPLE_STATE);

  const second = createWebController({ port });
  await assert.doesNotReject(() => second.start(SAMPLE_STATE));
  await second.stop();
  await blocker.stop();
});
