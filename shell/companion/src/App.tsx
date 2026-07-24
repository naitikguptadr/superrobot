import { useEffect, useState } from "react";
import type { JSX } from "react";
import { PipelineView } from "./PipelineView";
import type { PipelineState } from "./pipeline-types";

export function App(): JSX.Element {
  const [state, setState] = useState<PipelineState>([]);

  useEffect(() => {
    if (typeof WebSocket === "undefined") {
      return;
    }

    let ws: WebSocket;
    try {
      ws = new WebSocket(`ws://${window.location.host}/ws`);
    } catch (error) {
      console.error("[superrobot companion] websocket constructor error", error);
      return;
    }

    ws.onmessage = (event) => {
      setState(JSON.parse(event.data) as PipelineState);
    };

    ws.onerror = (event) => {
      console.error("[superrobot companion] websocket error", event);
    };

    return () => ws.close();
  }, []);

  return <PipelineView state={state} />;
}
