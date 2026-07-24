import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PipelineView } from "./PipelineView";
import type { PipelineState } from "./pipeline-types";

const SAMPLE_STATE: PipelineState = [
  { id: "scan", status: "done", detail: "langchain detected, 2 env vars, conf 0.85" },
  { id: "transform", status: "done", detail: "12 files generated" },
  { id: "validate", status: "active", detail: "" },
  { id: "deploy", status: "pending", detail: "" },
  { id: "receipt", status: "pending", detail: "" },
];

describe("PipelineView", () => {
  it("renders a detail message for a completed stage", () => {
    render(<PipelineView state={SAMPLE_STATE} />);
    expect(screen.getByText(/langchain detected/i)).toBeInTheDocument();
  });

  it("renders all 5 stage ids", () => {
    render(<PipelineView state={SAMPLE_STATE} />);
    for (const id of ["scan", "transform", "validate", "deploy", "receipt"]) {
      expect(screen.getByText(new RegExp(id, "i"))).toBeInTheDocument();
    }
  });

  it("renders an empty state message when given no stages", () => {
    render(<PipelineView state={[]} />);
    expect(screen.getByText(/no pipeline activity yet/i)).toBeInTheDocument();
  });
});
