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
  /**
   * Starts the server and resolves with the port it actually bound to.
   *
   * This is async (not sync) because `server.listen()` binds asynchronously:
   * a failure (e.g. EADDRINUSE) surfaces later via the server's `'error'`
   * event, not as a synchronous throw. Returning a Promise lets us wait for
   * either `'listening'` or `'error'` before resolving, so callers get an
   * accurate port (0 on failure) instead of racing `server.address()` before
   * the bind has actually settled. Never rejects -- a bind failure is caught
   * and logged, and the pipeline tool call that triggered this must not fail
   * because of it.
   */
  start(state: PipelineState): Promise<{ port: number }>;
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
    async start(state: PipelineState): Promise<{ port: number }> {
      latestState = state;

      const newServer = createServer((req, res) => {
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

      const newWss = new WebSocketServer({ server: newServer, path: "/ws" });
      newWss.on("connection", (ws) => {
        clients.add(ws);
        // Deliberately no initial `ws.send(latestState)` here: a client that
        // connects mid-pipeline picks up the current picture on the very
        // next update() broadcast. (Pushing eagerly here would race with --
        // and always win against -- a caller that triggers an update() from
        // its own "connection is open" handler, since this fires before the
        // client-side 'open' event ever could.)
        ws.on("close", () => clients.delete(ws));
      });

      const boundPort = await new Promise<number>((resolve) => {
        // Important: when a `server` option is passed to WebSocketServer, ws
        // itself subscribes to the underlying http.Server's 'error' event and
        // re-emits it on the WebSocketServer instance instead -- it does NOT
        // leave a listener-free 'error' on `newServer` for us to catch there.
        // A bind failure (e.g. EADDRINUSE) therefore surfaces as an 'error'
        // event on `newWss`, not on `newServer`; listening there only would
        // leave the http.Server's re-emitted event listener-free on `newWss`,
        // which Node treats as an unhandled 'error' and throws. Listen on
        // both to be safe regardless of which object ws chooses to emit on.
        newWss.once("error", (err) => {
          console.error("[superrobot] web companion failed to start:", err);
          resolve(0);
        });
        newServer.once("error", (err) => {
          console.error("[superrobot] web companion failed to start:", err);
          resolve(0);
        });
        newServer.once("listening", () => {
          const address = newServer.address();
          resolve(typeof address === "object" && address ? address.port : 0);
        });
        try {
          newServer.listen(options.port ?? 0);
        } catch (err) {
          console.error("[superrobot] web companion failed to start:", err);
          resolve(0);
        }
      });

      if (boundPort > 0) {
        server = newServer;
        wss = newWss;
      } else {
        // Bind failed -- make sure we don't leak the half-started server/wss,
        // and leave `server`/`wss` undefined so stop() is a no-op.
        newWss.close();
        newServer.removeAllListeners();
      }

      return { port: boundPort };
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
