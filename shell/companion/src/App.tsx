import { Component, useEffect, useState } from "react";
import type { ErrorInfo, JSX, ReactNode } from "react";
import { PipelineView } from "./PipelineView";
import { isPipelineState } from "./pipeline-types";
import type { PipelineState } from "./pipeline-types";

type ConnectionStatus = "connecting" | "open" | "closed";

const RECONNECT_DELAY_MS = 2000;

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

/** Defense-in-depth: if rendering the pipeline view throws for any
 * unanticipated reason, show a fallback message instead of letting React
 * unmount the whole tree to a blank screen. */
class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: unknown, errorInfo: ErrorInfo): void {
    console.error("[superrobot companion] render error", error, errorInfo);
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return <p>Something went wrong while rendering the pipeline view.</p>;
    }
    return this.props.children;
  }
}

function websocketUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws`;
}

export function App(): JSX.Element {
  const [state, setState] = useState<PipelineState>([]);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("connecting");

  useEffect(() => {
    if (typeof WebSocket === "undefined") {
      return;
    }

    let cancelled = false;
    let ws: WebSocket | undefined;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    function connect(): void {
      if (cancelled) {
        return;
      }

      setConnectionStatus("connecting");

      try {
        ws = new WebSocket(websocketUrl());
      } catch (error) {
        console.error("[superrobot companion] websocket constructor error", error);
        return;
      }

      ws.onopen = () => {
        setConnectionStatus("open");
      };

      ws.onmessage = (event) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(event.data);
        } catch (error) {
          console.error("[superrobot companion] invalid websocket JSON payload", error);
          return;
        }

        if (!isPipelineState(parsed)) {
          console.error("[superrobot companion] malformed pipeline state payload, ignoring", parsed);
          return;
        }

        setState(parsed);
      };

      ws.onerror = (event) => {
        console.error("[superrobot companion] websocket error", event);
      };

      ws.onclose = () => {
        setConnectionStatus("closed");
        if (!cancelled) {
          if (reconnectTimer) {
            clearTimeout(reconnectTimer);
          }
          reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
        }
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      ws?.close();
    };
  }, []);

  return (
    <>
      {connectionStatus === "closed" && <div role="status">Disconnected — reconnecting...</div>}
      <ErrorBoundary>
        <PipelineView state={state} />
      </ErrorBoundary>
    </>
  );
}
