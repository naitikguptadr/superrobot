import { createServer, type Server } from "node:http";
import { readFile } from "node:fs/promises";
import { join, extname, dirname, resolve, sep } from "node:path";
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
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".svg": "image/svg+xml",
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
          // Parse with a base so req.url's query/hash (e.g. "/?t=123") don't
          // leak into the "/" check or the file path -- .pathname is
          // stripped of both.
          const pathname = new URL(req.url ?? "/", "http://localhost").pathname;
          const urlPath = pathname === "/" ? "/index.html" : pathname;
          const filePath = resolve(join(COMPANION_DIST_DIR, urlPath));
          // Sandbox check: a request like GET /../../package.json would
          // otherwise resolve outside COMPANION_DIST_DIR and let the process
          // read (and serve) arbitrary files on disk. Require the resolved
          // path to still be COMPANION_DIST_DIR itself or a descendant of it
          // -- comparing against `${dir}${sep}` (rather than a bare prefix
          // check) so a sibling directory that merely shares the prefix
          // (e.g. "companion/dist-evil") isn't mistaken for a descendant.
          if (filePath !== COMPANION_DIST_DIR && !filePath.startsWith(COMPANION_DIST_DIR + sep)) {
            res.writeHead(403);
            res.end("Forbidden");
            return;
          }
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

      // Assign to the outer closure vars immediately -- before awaiting the
      // listen-or-error resolution below -- so that a stop() call racing an
      // in-flight start() (e.g. session_shutdown firing while the bind is
      // still pending) can always find and close these, rather than seeing
      // `undefined` and silently no-op'ing while this half-bound server goes
      // on to bind successfully with no reachable reference left to close it
      // (a permanent leak). http.Server#close()/WebSocketServer#close() are
      // safe to call on a server that hasn't finished binding yet -- they
      // just abort (or clean up after) the pending listen.
      const newWss = new WebSocketServer({ server: newServer, path: "/ws" });
      server = newServer;
      wss = newWss;
      newWss.on("connection", (ws) => {
        clients.add(ws);
        // Push the current state immediately so a client that connects
        // mid-pipeline (e.g. during a 15-20 minute "deploy" stage) sees the
        // live picture right away instead of staring at a blank view until
        // the next update() call happens to fire.
        ws.send(JSON.stringify(latestState));
        ws.on("close", () => clients.delete(ws));
        // Without this, a malformed/protocol-violating frame from the client
        // emits 'error' on `ws` with zero listeners -- which Node's
        // EventEmitter treats as an uncaught exception, crashing the entire
        // host process. Terminate just this connection instead.
        ws.on("error", () => ws.terminate());
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
          // Bind to the IPv4 loopback address explicitly. Without a host,
          // Node binds to the unspecified address ('::' / all interfaces),
          // which would make this local-dev-only companion UI reachable from
          // the entire LAN.
          newServer.listen(options.port ?? 0, "127.0.0.1");
        } catch (err) {
          console.error("[superrobot] web companion failed to start:", err);
          resolve(0);
        }
      });

      if (boundPort === 0) {
        // Bind failed outright, or a concurrent stop() aborted it mid-flight
        // -- make sure we don't leak the half-started server/wss, and only
        // clear the outer refs if they still point at *this* attempt (a
        // concurrent stop() may already have closed and/or cleared them).
        newWss.close();
        newServer.removeAllListeners();
        if (server === newServer) server = undefined;
        if (wss === newWss) wss = undefined;
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
