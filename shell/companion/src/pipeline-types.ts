export type StageId = "scan" | "transform" | "validate" | "deploy" | "receipt";
export type StageStatus = "pending" | "active" | "done" | "failed";

export interface StageState {
  id: StageId;
  status: StageStatus;
  detail: string;
}

export type PipelineState = StageState[];
