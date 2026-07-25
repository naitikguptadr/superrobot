export type StageId = "scan" | "transform" | "validate" | "deploy" | "receipt";
export type StageStatus = "pending" | "active" | "done" | "failed";

export interface StageState {
  id: StageId;
  status: StageStatus;
  detail: string;
}

export type PipelineState = StageState[];

const STAGE_IDS: readonly StageId[] = ["scan", "transform", "validate", "deploy", "receipt"];
const STAGE_STATUSES: readonly StageStatus[] = ["pending", "active", "done", "failed"];

function isStageState(value: unknown): value is StageState {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    STAGE_IDS.includes(candidate.id as StageId) &&
    STAGE_STATUSES.includes(candidate.status as StageStatus) &&
    typeof candidate.detail === "string"
  );
}

/** Runtime type guard for data coming off the wire (e.g. a WebSocket
 * message). `JSON.parse` returns `any`, so a plain `as PipelineState`
 * type assertion provides no actual safety -- malformed payloads must be
 * validated at runtime before being trusted as a `PipelineState`. */
export function isPipelineState(value: unknown): value is PipelineState {
  return Array.isArray(value) && value.every(isStageState);
}
