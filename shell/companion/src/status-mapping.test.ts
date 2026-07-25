import { describe, expect, it } from "vitest";
import { badgePropsForStatus, labelForStatus } from "./status-mapping";
import type { StageStatus } from "./pipeline-types";

describe("badgePropsForStatus", () => {
  it("maps pending to a plain badge", () => {
    expect(badgePropsForStatus("pending")).toEqual({ plain: true });
  });

  it("maps active to an info badge with a loading indicator", () => {
    expect(badgePropsForStatus("active")).toEqual({ info: true, isLoading: true });
  });

  it("maps done to a success badge", () => {
    expect(badgePropsForStatus("done")).toEqual({ success: true });
  });

  it("maps failed to an error badge", () => {
    expect(badgePropsForStatus("failed")).toEqual({ error: true });
  });

  it("falls back to a plain badge for an unrecognized status", () => {
    expect(badgePropsForStatus("unknown" as StageStatus)).toEqual({ plain: true });
  });
});

describe("labelForStatus", () => {
  it("returns a distinguishable human-readable label for each known status", () => {
    expect(labelForStatus("pending")).toMatch(/pending/i);
    expect(labelForStatus("active")).toMatch(/progress/i);
    expect(labelForStatus("done")).toMatch(/done/i);
    expect(labelForStatus("failed")).toMatch(/failed/i);
  });

  it("falls back to a sensible label for an unrecognized status", () => {
    expect(labelForStatus("unknown" as StageStatus)).toBeTruthy();
  });
});
