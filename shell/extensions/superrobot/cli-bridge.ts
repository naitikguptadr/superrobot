export type ExecFn = (
  args: string[],
  opts?: { cwd?: string },
) => Promise<{ stdout: string; stderr: string; code: number }>;

export type CliResult<T> =
  | { ok: true; data: T }
  | { ok: false; reason: "not_found"; message: string }
  | { ok: false; reason: "parse_error"; message: string }
  | { ok: false; reason: "cli_error"; message: string; data?: T };

const MAX_ERROR_TAIL = 2000;

async function runJson<T>(
  exec: ExecFn,
  args: string[],
  opts?: { cwd?: string },
): Promise<CliResult<T>> {
  let raw: { stdout: string; stderr: string; code: number };
  try {
    raw = await exec([...args, "--json"], opts);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    if (message.includes("ENOENT")) {
      return { ok: false, reason: "not_found", message: "superrobot CLI not found on PATH" };
    }
    return { ok: false, reason: "cli_error", message };
  }

  let parsed: T | undefined;
  try {
    parsed = JSON.parse(raw.stdout) as T;
  } catch {
    const tail = (raw.stderr || raw.stdout).slice(-MAX_ERROR_TAIL);
    return { ok: false, reason: "parse_error", message: `superrobot did not return JSON: ${tail}` };
  }

  if (raw.code !== 0) {
    return {
      ok: false,
      reason: "cli_error",
      message: raw.stderr.slice(-MAX_ERROR_TAIL) || `superrobot exited with code ${raw.code}`,
      data: parsed,
    };
  }
  return { ok: true, data: parsed };
}

export function createCliBridge(exec: ExecFn) {
  return {
    scan(path: string, cwd?: string) {
      return runJson<unknown>(exec, ["scan", path], { cwd });
    },

    transform(path: string, opts: { outputDir?: string; skipEval?: boolean } = {}, cwd?: string) {
      const args = ["transform", path];
      if (opts.outputDir) args.push("--output-dir", opts.outputDir);
      if (opts.skipEval) args.push("--skip-eval");
      return runJson<unknown>(exec, args, { cwd });
    },

    validate(path: string, source?: string, cwd?: string) {
      const args = ["validate", path];
      if (source) args.push("--source", source);
      return runJson<unknown>(exec, args, { cwd });
    },

    deploy(
      path: string,
      target: "agent-app" | "workload",
      opts: { imageUri?: string; waive?: boolean } = {},
      cwd?: string,
    ) {
      const args = ["deploy", path, "--target", target];
      if (opts.imageUri) args.push("--image-uri", opts.imageUri);
      if (opts.waive) args.push("--waive");
      return runJson<unknown>(exec, args, { cwd });
    },

    receiptShow(id?: string, cwd?: string) {
      const args = ["receipt", "show"];
      if (id) args.push(id);
      return runJson<unknown>(exec, args, { cwd });
    },

    receiptOperations(target?: string, cwd?: string) {
      const args = ["receipt", "operations"];
      if (target) args.push("--target", target);
      return runJson<unknown>(exec, args, { cwd });
    },

    receiptDiagnose(id: string, cwd?: string) {
      return runJson<unknown>(exec, ["receipt", "diagnose", id], { cwd });
    },

    receiptReplace(id: string, cwd?: string) {
      return runJson<unknown>(exec, ["receipt", "replace", id], { cwd });
    },

    memoryEnsure(name: string, cwd?: string) {
      return runJson<unknown>(exec, ["memory", "ensure", name], { cwd });
    },
  };
}

export type CliBridge = ReturnType<typeof createCliBridge>;
