import "@testing-library/jest-dom";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

const RECONNECT_DELAY_MS = 2000;

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  url: string;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }

  emitMessage(data: unknown): void {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

describe("App", () => {
  const originalWebSocket = globalThis.WebSocket;

  beforeEach(() => {
    MockWebSocket.instances = [];
    // @ts-expect-error -- test double, not a full WebSocket implementation
    globalThis.WebSocket = MockWebSocket;
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders without throwing and shows the empty-state message before any data arrives", () => {
    render(<App />);
    expect(screen.getByText(/no pipeline activity yet/i)).toBeInTheDocument();
  });

  it("ignores a non-array websocket payload and keeps showing the last known-good state instead of crashing", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<App />);
    const ws = MockWebSocket.instances[0];

    act(() => {
      ws.emitMessage([{ id: "scan", status: "done", detail: "langchain detected" }]);
    });
    expect(screen.getByText(/langchain detected/i)).toBeInTheDocument();

    act(() => {
      ws.emitMessage({ notAnArray: true });
    });

    // Should still show the last known-good state, not have crashed to blank.
    expect(screen.getByText(/langchain detected/i)).toBeInTheDocument();
  });

  it("ignores a well-formed-JSON-but-wrong-shape payload without crashing", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<App />);
    const ws = MockWebSocket.instances[0];

    act(() => {
      ws.emitMessage([{ id: "scan", status: "done", detail: "langchain detected" }]);
    });

    act(() => {
      // array of objects missing `status`
      ws.emitMessage([{ id: "scan", detail: "missing status" }]);
    });

    expect(screen.getByText(/langchain detected/i)).toBeInTheDocument();
  });

  it("shows a disconnect indicator when the websocket closes", () => {
    render(<App />);
    const ws = MockWebSocket.instances[0];

    act(() => {
      ws.onclose?.();
    });

    expect(screen.getByText(/disconnected/i)).toBeInTheDocument();
  });

  it("uses a secure wss:// scheme when the page is served over https", () => {
    vi.stubGlobal("location", { ...window.location, protocol: "https:" });

    render(<App />);

    expect(MockWebSocket.instances[0].url).toMatch(/^wss:\/\//);
  });

  it("uses an insecure ws:// scheme when the page is served over http", () => {
    vi.stubGlobal("location", { ...window.location, protocol: "http:" });

    render(<App />);

    expect(MockWebSocket.instances[0].url).toMatch(/^ws:\/\//);
  });

  it("schedules only one reconnect even if onclose fires multiple times in quick succession", () => {
    vi.useFakeTimers();
    render(<App />);
    const firstWs = MockWebSocket.instances[0];

    // First close event
    act(() => {
      firstWs.onclose?.();
    });

    // Second close event (e.g. from a misbehaving proxy or polyfill) fires immediately
    act(() => {
      firstWs.onclose?.();
    });

    // Advance time to after the reconnect delay, but not double the delay
    act(() => {
      vi.advanceTimersByTime(RECONNECT_DELAY_MS);
    });

    // Should have created only the initial WS and one reconnect WS, not two
    expect(MockWebSocket.instances).toHaveLength(2);

    vi.useRealTimers();
  });
});
