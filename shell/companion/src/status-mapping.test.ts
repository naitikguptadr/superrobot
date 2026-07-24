import { describe, expect, it } from "vitest";
import { badgePropsForStatus } from "./status-mapping";

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
});
