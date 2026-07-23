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

  function draw(): void {
    if (!currentState) return;
    ctx.ui.setWidget(WIDGET_KEY, renderRailLines(currentState, Date.now() - startedAt));
  }

  return {
    start(state: PipelineState) {
      currentState = state;
      startedAt = Date.now();
      draw();
      if (!interval) interval = setInterval(draw, 90);
    },
    update(state: PipelineState) {
      currentState = state;
      draw();
    },
    stop() {
      if (interval) {
        clearInterval(interval);
        interval = undefined;
      }
      currentState = undefined;
      ctx.ui.setWidget(WIDGET_KEY, undefined);
    },
  };
}
