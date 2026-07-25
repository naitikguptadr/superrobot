import type { StageStatus } from "./pipeline-types";

export interface BadgeStatusProps {
  plain?: boolean;
  info?: boolean;
  success?: boolean;
  error?: boolean;
  isLoading?: boolean;
}

/** Map the existing 4-value StageStatus to @datarobot/design-system's
 * Badge boolean status props (verified: Badge has no single `status`
 * enum prop, it's boolean flags -- success/error/info/warning/plain).
 */
export function badgePropsForStatus(status: StageStatus): BadgeStatusProps {
  switch (status) {
    case "pending":
      return { plain: true };
    case "active":
      return { info: true, isLoading: true };
    case "done":
      return { success: true };
    case "failed":
      return { error: true };
    default:
      // Defensive fallback: even with upstream runtime validation, this
      // guards against a future status value reaching this function.
      return { plain: true };
  }
}

/** Human-readable label for a stage status, used as the Badge's visible
 * text/accessible name -- status must not be conveyed by color alone. */
export function labelForStatus(status: StageStatus): string {
  switch (status) {
    case "pending":
      return "Pending";
    case "active":
      return "In progress";
    case "done":
      return "Done";
    case "failed":
      return "Failed";
    default:
      return "Unknown";
  }
}
