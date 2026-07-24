import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("App", () => {
  it("renders without throwing and shows the empty-state message before any data arrives", () => {
    render(<App />);
    expect(screen.getByText(/no pipeline activity yet/i)).toBeInTheDocument();
  });
});
