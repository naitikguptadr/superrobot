export type StageId = "scan" | "transform" | "validate" | "deploy" | "receipt";
export type StageStatus = "pending" | "active" | "done" | "failed";

export interface StageState {
  id: StageId;
  status: StageStatus;
  detail: string;
}

export type PipelineState = StageState[];

const STAGE_ORDER: StageId[] = ["scan", "transform", "validate", "deploy", "receipt"];

export function freshPipeline(): PipelineState {
  return STAGE_ORDER.map((id) => ({ id, status: "pending", detail: "" }));
}

function updateStage(
  state: PipelineState,
  id: StageId,
  status: StageStatus,
  detail: string,
): PipelineState {
  return state.map((stage) => (stage.id === id ? { ...stage, status, detail } : stage));
}

export function withStageActive(state: PipelineState, id: StageId, detail = ""): PipelineState {
  return updateStage(state, id, "active", detail);
}

export function withStageDone(state: PipelineState, id: StageId, detail: string): PipelineState {
  return updateStage(state, id, "done", detail);
}

export function withStageFailed(state: PipelineState, id: StageId, detail: string): PipelineState {
  return updateStage(state, id, "failed", detail);
}

export function hasActiveStage(state: PipelineState): boolean {
  return state.some((stage) => stage.status === "active");
}
