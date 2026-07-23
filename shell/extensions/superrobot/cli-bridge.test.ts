import { test } from "node:test";
import assert from "node:assert/strict";
import { createCliBridge, type ExecFn } from "./cli-bridge.ts";

function fakeExec(
  impl: (args: string[]) => { stdout: string; stderr: string; code: number },
): ExecFn {
  return async (args: string[]) => impl(args);
}

test("scan: builds args and parses JSON on success", async () => {
  let capturedArgs: string[] = [];
  const exec = fakeExec((args) => {
    capturedArgs = args;
    return { stdout: JSON.stringify({ detected_framework: "langchain" }), stderr: "", code: 0 };
  });
  const cli = createCliBridge(exec);
  const result = await cli.scan("tests/fixtures/langchain_agent");
  assert.deepEqual(capturedArgs, ["scan", "tests/fixtures/langchain_agent", "--json"]);
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal((result.data as { detected_framework: string }).detected_framework, "langchain");
  }
});

test("validate: non-zero exit with valid JSON is a domain result, not an error", async () => {
  const exec = fakeExec(() => ({
    stdout: JSON.stringify({
      findings: [{ rule: "flat_imports", severity: "blocking", message: "bad import" }],
    }),
    stderr: "",
    code: 1,
  }));
  const cli = createCliBridge(exec);
  const result = await cli.validate("/tmp/sr-out");
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.reason, "cli_error");
    assert.ok(result.data, "data should still be attached for a domain-level non-zero exit");
  }
});

test("exec throwing ENOENT is reported as not_found", async () => {
  const exec: ExecFn = async () => {
    throw new Error("spawn superrobot ENOENT");
  };
  const cli = createCliBridge(exec);
  const result = await cli.scan("some/path");
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.reason, "not_found");
});

test("non-JSON stdout is reported as parse_error", async () => {
  const exec = fakeExec(() => ({ stdout: "not json", stderr: "", code: 0 }));
  const cli = createCliBridge(exec);
  const result = await cli.scan("some/path");
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.reason, "parse_error");
});

test("deploy: builds target/waive/image-uri flags", async () => {
  let capturedArgs: string[] = [];
  const exec = fakeExec((args) => {
    capturedArgs = args;
    return { stdout: JSON.stringify({ success: true }), stderr: "", code: 0 };
  });
  const cli = createCliBridge(exec);
  await cli.deploy("/tmp/sr-out", "workload", { imageUri: "registry/img:tag", waive: true });
  assert.deepEqual(capturedArgs, [
    "deploy",
    "/tmp/sr-out",
    "--target",
    "workload",
    "--image-uri",
    "registry/img:tag",
    "--waive",
    "--json",
  ]);
});

test("receipts: show/operations/diagnose/replace build distinct arg shapes", async () => {
  const seen: string[][] = [];
  const exec = fakeExec((args) => {
    seen.push(args);
    return { stdout: JSON.stringify({}), stderr: "", code: 0 };
  });
  const cli = createCliBridge(exec);
  await cli.receiptShow();
  await cli.receiptShow("abc123");
  await cli.receiptOperations("agent-app");
  await cli.receiptDiagnose("abc123");
  await cli.receiptReplace("abc123");
  assert.deepEqual(seen, [
    ["receipt", "show", "--json"],
    ["receipt", "show", "abc123", "--json"],
    ["receipt", "operations", "--target", "agent-app", "--json"],
    ["receipt", "diagnose", "abc123", "--json"],
    ["receipt", "replace", "abc123", "--json"],
  ]);
});

test("receiptReplace: builds --waive flag when requested", async () => {
  let capturedArgs: string[] = [];
  const exec = fakeExec((args) => {
    capturedArgs = args;
    return { stdout: JSON.stringify({}), stderr: "", code: 0 };
  });
  const cli = createCliBridge(exec);
  await cli.receiptReplace("abc123", { waive: true });
  assert.deepEqual(capturedArgs, ["receipt", "replace", "abc123", "--waive", "--json"]);
});
