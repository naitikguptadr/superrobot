import type { ExtensionContext } from "@mariozechner/pi-coding-agent";
import { boxLines } from "./box.ts";
import type { PipelineState, StageId } from "./pipeline-state.ts";
import { railColor, spinnerFrame } from "./render.ts";

const STAGE_LABELS: Record<StageId, string> = {
  scan: "Scan",
  transform: "Transform",
  validate: "Validate",
  deploy: "Deploy",
  receipt: "Receipt",
};

const WIDGET_KEY = "superrobot-rail";

function stageRow(stage: PipelineState[number], elapsedMs: number): string {
  const label = STAGE_LABELS[stage.id].padEnd(10);
  let glyph: string;
  switch (stage.status) {
    case "done":
      glyph = railColor("green", "✓");
      break;
    case "failed":
      glyph = railColor("red", "✗");
      break;
    case "active":
      glyph = railColor("gold", spinnerFrame(elapsedMs));
      break;
    default:
      glyph = railColor("slate", "○");
  }
  const detail = stage.detail ? railColor("slate", stage.detail) : "";
  return `${glyph} ${label}${detail}`;
}

export function renderRailLines(state: PipelineState, elapsedMs: number): string[] {
  const rows = state.map((stage) => stageRow(stage, elapsedMs));
  return [railColor("slate", "PIPELINE"), ...boxLines(rows)];
}

export interface RailController {
  start(state: PipelineState): void;
  update(state: PipelineState): void;
  stop(): void;
}

/** Drives ctx.ui.setWidget() on a ~90ms tick while a stage is active, so the spinner glyph animates. */
export function createRailController(ctx: ExtensionContext): RailController {
  let interval: ReturnType<typeof setInterval> | undefined;
  let startedAt = 0;
  let currentState: PipelineState | undefined;
  let stopped = false;

  function clearTimer(): void {
    if (interval) {
      clearInterval(interval);
      interval = undefined;
    }
  }

  function draw(): void {
    if (stopped || !currentState) return;
    try {
      ctx.ui.setWidget(WIDGET_KEY, renderRailLines(currentState, Date.now() - startedAt));
    } catch {
      // ctx becomes unusable once the extension runtime it belongs to is torn
      // down (session switch/fork/compact/shutdown/reload) -- accessing
      // ctx.ui after that throws. A stale timer tick hitting that case isn't
      // a bug to crash on; treat it the same as an explicit stop().
      stopped = true;
      clearTimer();
    }
  }

  return {
    start(state: PipelineState) {
      if (stopped) return;
      currentState = state;
      startedAt = Date.now();
      draw();
      // draw() may itself flip `stopped` (see its catch) if ctx turned out to
      // be invalid on this very first draw -- don't arm a ticking interval
      // in that case, or it would fire forever as a no-op and keep the
      // process alive.
      if (!interval && !stopped) interval = setInterval(draw, 90);
    },
    update(state: PipelineState) {
      if (stopped) return;
      currentState = state;
      draw();
    },
    stop() {
      if (stopped) return;
      stopped = true;
      clearTimer();
      currentState = undefined;
      try {
        ctx.ui.setWidget(WIDGET_KEY, undefined);
      } catch {
        // Same rationale as draw(): ctx may already be invalid by the time
        // stop() runs (e.g. called from a session_shutdown handler).
      }
    },
  };
}
