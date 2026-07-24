import { test } from "node:test";
import assert from "node:assert/strict";
import { visibleWidth } from "@mariozechner/pi-tui";
import { boxLines } from "./box.ts";

test("all lines in the box have equal visible width", () => {
  const lines = boxLines(["short", "a much longer row of text", "mid"]);
  const widths = new Set(lines.map((l) => visibleWidth(l)));
  assert.equal(widths.size, 1, `expected one uniform width, got: ${[...widths]}`);
});

test("box has a top border, one row per input line, and a bottom border", () => {
  const lines = boxLines(["a", "b", "c"]);
  assert.equal(lines.length, 5); // top + 3 rows + bottom
  assert.ok(lines[0].startsWith("┌"));
  assert.ok(lines[0].endsWith("┐"));
  assert.ok(lines[lines.length - 1].startsWith("└"));
  assert.ok(lines[lines.length - 1].endsWith("┘"));
});

test("rows with embedded ANSI color codes still align by visible width", () => {
  const colored = `\x1b[38;2;61;219;217mred herring\x1b[0m`; // visually 11 chars, longer as a raw string
  const lines = boxLines([colored, "short"]);
  const widths = new Set(lines.slice(1, -1).map((l) => visibleWidth(l)));
  assert.equal(widths.size, 1, "ANSI codes must not be counted as visible width");
});

test("minWidth pads narrower content up to the requested width", () => {
  const lines = boxLines(["hi"], 20);
  assert.ok(visibleWidth(lines[1]) >= 20);
});

test("a row containing a newline is flattened to a single line, not split across rows", () => {
  const lines = boxLines(["one\ntwo", "short"]);
  // top + 2 rows + bottom -- a newline must not produce an extra unbordered line
  assert.equal(lines.length, 4);
  for (const line of lines.slice(1, -1)) {
    assert.ok(line.startsWith("│") && line.endsWith("│"), `row lost its border: ${JSON.stringify(line)}`);
  }
  const widths = new Set(lines.map((l) => visibleWidth(l)));
  assert.equal(widths.size, 1, "newline-flattened row must still align with the rest of the box");
});

test("an empty rows array still produces a valid top/bottom-only box", () => {
  const lines = boxLines([]);
  assert.equal(lines.length, 2);
  assert.ok(lines[0].startsWith("┌") && lines[0].endsWith("┐"));
  assert.ok(lines[1].startsWith("└") && lines[1].endsWith("┘"));
});
