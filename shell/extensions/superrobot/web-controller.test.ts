import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import net from "node:net";
import { spawn } from "node:child_process";
import { writeFile, rm } from "node:fs/promises";
import os from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import WebSocket from "ws";
import { createWebController } from "./web-controller.ts";
import type { PipelineState } from "./pipeline-state.ts";

const HERE = dirname(fileURLToPath(import.meta.url));

/** Issues a raw HTTP GET (no path normalization) so path-traversal payloads reach the server byte-for-byte. */
function rawGet(port: number, rawPath: string): Promise<{ statusCode: number; body: string }> {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, path: rawPath, method: "GET" }, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (chunk: Buffer) => chunks.push(chunk));
      res.on("end", () => resolve({ statusCode: res.statusCode ?? 0, body: Buffer.concat(chunks).toString("utf8") }));
    });
    req.on("error", reject);
    req.end();
  });
}

/** Spawns `node <scriptPath>` and resolves with its exit code + collected stdout, killing it after `timeoutMs`. */
function runNodeScript(scriptPath: string, timeoutMs = 5000): Promise<{ code: number | null; stdout: string }> {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [scriptPath], { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    child.stdout.on("data", (chunk: Buffer) => (stdout += chunk.toString("utf8")));
    child.stderr.on("data", (chunk: Buffer) => (stdout += chunk.toString("utf8")));
    const timer = setTimeout(() => child.kill(), timeoutMs);
    child.on("exit", (code) => {
      clearTimeout(timer);
      resolve({ code, stdout });
    });
  });
}

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

test("a path-traversal request cannot escape COMPANION_DIST_DIR to read arbitrary files", async () => {
  const controller = createWebController({ port: 0 });
  const { port } = await controller.start(SAMPLE_STATE);

  try {
    // COMPANION_DIST_DIR is shell/companion/dist; two levels up is shell/,
    // which has a real, readable package.json -- if this ever returns 200
    // with that file's contents, the traversal guard has regressed.
    const res = await rawGet(port, "/../../package.json");
    assert.notEqual(res.statusCode, 200, "path traversal must not return 200");
    assert.ok(!res.body.includes("superrobot-shell"), "response must not leak package.json contents");
  } finally {
    await controller.stop();
  }
});

test("web controller binds only to 127.0.0.1, not all interfaces", async () => {
  const controller = createWebController({ port: 0 });
  const { port } = await controller.start(SAMPLE_STATE);

  try {
    // Confirm loopback still works...
    await new Promise<void>((resolve, reject) => {
      const sock = net.connect(port, "127.0.0.1", () => {
        sock.end();
        resolve();
      });
      sock.on("error", reject);
    });

    // ...but nothing is listening on the IPv6 loopback, which would only be
    // reachable if the server had bound to the wildcard '::' address instead
    // of the explicit IPv4 loopback.
    await new Promise<void>((resolve, reject) => {
      const sock = net.connect({ port, host: "::1" }, () => {
        sock.end();
        reject(new Error("connected on ::1 -- server is not restricted to 127.0.0.1"));
      });
      sock.on("error", () => resolve());
    });
  } finally {
    await controller.stop();
  }
});

test("stop() called while start() is still in-flight does not leak the server", async () => {
  const controller = createWebController({ port: 0 });
  const startPromise = controller.start(SAMPLE_STATE);
  await controller.stop();
  const { port } = await startPromise;

  if (port > 0) {
    await new Promise<void>((resolve, reject) => {
      const sock = net.connect(port, "127.0.0.1", () => {
        sock.end();
        reject(new Error(`server is still listening on port ${port} after stop()-during-start()`));
      });
      sock.on("error", () => resolve());
    });
  }
  // port === 0 (bind itself lost the race entirely) is also an acceptable
  // outcome here -- the property under test is "nothing is left listening",
  // not any particular value of `port`.
});

test("a query string does not break the static file server", async () => {
  const controller = createWebController({ port: 0 });
  const { port } = await controller.start(SAMPLE_STATE);

  try {
    const res = await rawGet(port, "/?t=123");
    assert.equal(res.statusCode, 200);
    assert.ok(res.body.length > 0);

    const res2 = await rawGet(port, "/index.html?v=abc");
    assert.equal(res2.statusCode, 200);
    assert.equal(res2.body, res.body);
  } finally {
    await controller.stop();
  }
});

test("a client-triggered WebSocket protocol error does not crash the server process", async () => {
  const script = `
    import { createWebController } from ${JSON.stringify(pathToFileURL(join(HERE, "web-controller.ts")).href)};
    import net from "node:net";
    import crypto from "node:crypto";

    const controller = createWebController({ port: 0 });
    const { port } = await controller.start([]);

    const key = crypto.randomBytes(16).toString("base64");
    const socket = net.connect(port, "127.0.0.1", () => {
      socket.write(
        "GET /ws HTTP/1.1\\r\\n" +
        "Host: 127.0.0.1:" + port + "\\r\\n" +
        "Upgrade: websocket\\r\\n" +
        "Connection: Upgrade\\r\\n" +
        "Sec-WebSocket-Key: " + key + "\\r\\n" +
        "Sec-WebSocket-Version: 13\\r\\n\\r\\n"
      );
    });

    let handshakeDone = false;
    socket.on("data", (data) => {
      if (!handshakeDone && data.toString("latin1").includes(" 101 ")) {
        handshakeDone = true;
        // An unmasked client->server frame violates RFC 6455 (clients MUST
        // mask their frames), which is a deterministic way to make the
        // server's per-connection ws Receiver emit a protocol 'error'.
        socket.write(Buffer.from([0x81, 0x01, 0x00]));
      }
    });

    setTimeout(async () => {
      console.log("SURVIVED");
      await controller.stop();
      process.exit(0);
    }, 500);
  `;
  const scriptPath = join(os.tmpdir(), `ws-crash-repro-${process.pid}-${Date.now()}.mjs`);
  await writeFile(scriptPath, script, "utf8");
  try {
    const { code, stdout } = await runNodeScript(scriptPath);
    assert.match(stdout, /SURVIVED/, `child process output:\n${stdout}`);
    assert.equal(code, 0, `child process should exit cleanly, not crash; output:\n${stdout}`);
  } finally {
    await rm(scriptPath, { force: true });
  }
});
