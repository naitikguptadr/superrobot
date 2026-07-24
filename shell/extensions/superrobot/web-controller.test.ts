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

  // A connecting client receives two messages in order: (1) the initial
  // snapshot pushed eagerly on connect (the state passed to start()), then
  // (2) the broadcast triggered by the update() call below. We collect both
  // and assert on the last one to genuinely prove update() delivers fresh
  // state to an already-connected client.
  const messages = await new Promise<PipelineState[]>((resolve, reject) => {
    const ws = new WebSocket(`ws://127.0.0.1:${port}/ws`);
    const received: PipelineState[] = [];
    ws.on("message", (data) => {
      received.push(JSON.parse(data.toString()));
      if (received.length === 2) {
        resolve(received);
        ws.close();
      }
    });
    ws.on("error", reject);
    ws.on("open", () => {
      controller.update(SAMPLE_STATE.map((s) => (s.id === "transform" ? { ...s, status: "active" } : s)));
    });
  });

  const [initial, updated] = messages;
  assert.equal(initial.find((s) => s.id === "transform")?.status, "pending");
  assert.equal(updated.find((s) => s.id === "transform")?.status, "active");

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
