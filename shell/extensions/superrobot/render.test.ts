import { test } from "node:test";
import assert from "node:assert/strict";
import { railColor, spinnerFrame } from "./render.ts";

test("railColor wraps text in a truecolor ANSI escape and resets after", () => {
  const result = railColor("teal", "hello");
  assert.ok(result.startsWith("\x1b[38;2;"));
  assert.ok(result.endsWith("\x1b[0m"));
  assert.ok(result.includes("hello"));
});

test("railColor falls back to plain text for an unknown color name", () => {
  // @ts-expect-error -- deliberately testing runtime behavior for an invalid name
  const result = railColor("not-a-color", "hello");
  assert.equal(result, "hello");
});

test("railColor produces the exact RGB channels for teal's real theme hex (#3DDBD9)", () => {
  const result = railColor("teal", "x");
  // #3DDBD9 -> 61;219;217 -- pins the hex-to-RGB slicing against a known value
  // so a swapped-channel or off-by-one slicing bug would fail this test.
  assert.equal(result, "\x1b[38;2;61;219;217mx\x1b[0m");
});

test("spinnerFrame cycles through frames as elapsed time increases", () => {
  const frame0 = spinnerFrame(0);
  const frame1 = spinnerFrame(90);
  const frame2 = spinnerFrame(180);
  const frame4 = spinnerFrame(360); // exactly one full cycle later at 4 frames * 90ms
  assert.notEqual(frame0, frame1);
  assert.notEqual(frame1, frame2);
  assert.equal(frame0, frame4);
});
