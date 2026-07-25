import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
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

const ALL_PENDING_STATE: PipelineState = [
  { id: "scan", status: "pending", detail: "" },
  { id: "transform", status: "pending", detail: "" },
  { id: "validate", status: "pending", detail: "" },
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

  it("renders the stepper's step buttons as disabled so the read-only stepper is not clickable", () => {
    const { container } = render(<PipelineView state={SAMPLE_STATE} />);
    const stepButtons = container.querySelectorAll('.stepper button[test-id$="-item"]');
    expect(stepButtons.length).toBeGreaterThan(0);
    for (const button of stepButtons) {
      expect(button).toBeDisabled();
    }
  });

  it("does not corrupt the active step's visited/icon state when a completed step's (disabled) button is clicked", () => {
    const { container } = render(<PipelineView state={SAMPLE_STATE} />);
    const scanButton = container.querySelector('[test-id="stepper-scan-item"]');
    const validateIcon = container.querySelector('[test-id="stepper-validate-icon-btn"]');
    expect(scanButton).not.toBeNull();
    expect(validateIcon).not.toBeNull();

    expect(validateIcon).not.toHaveClass("visited");

    fireEvent.click(scanButton as Element);

    expect(validateIcon).not.toHaveClass("visited");
  });

  it("does not mark any stage as completed when no stage is active yet (all pending)", () => {
    const { container } = render(<PipelineView state={ALL_PENDING_STATE} />);
    const scanIcon = container.querySelector('[test-id="stepper-scan-icon-btn"]');
    expect(scanIcon).not.toBeNull();
    expect(scanIcon).not.toHaveClass("completed");
  });

  it("renders visible, distinguishable status text for each stage status", () => {
    const oneStage = (status: PipelineState[number]["status"]): PipelineState => [
      { id: "scan", status, detail: "" },
    ];

    const { unmount: unmountPending } = render(<PipelineView state={oneStage("pending")} />);
    expect(screen.getByText(/pending/i)).toBeInTheDocument();
    unmountPending();

    const { unmount: unmountActive } = render(<PipelineView state={oneStage("active")} />);
    expect(screen.getByText(/in progress/i)).toBeInTheDocument();
    unmountActive();

    const { unmount: unmountDone } = render(<PipelineView state={oneStage("done")} />);
    expect(screen.getByText(/done/i)).toBeInTheDocument();
    unmountDone();

    render(<PipelineView state={oneStage("failed")} />);
    expect(screen.getByText(/failed/i)).toBeInTheDocument();
  });
});
