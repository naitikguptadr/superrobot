/**
 * Bridge to the IR pipeline (`python -m superrobot.ir`) and the five harness
 * tools built on it: sr_extract, sr_report, sr_decisions, sr_spec, sr_scaffold.
 *
 * Contract with the Python side: every subcommand writes a single JSON object
 * to stdout and nothing else. On failure it writes {"error", "kind"} to stdout
 * and exits non-zero -- so stdout is ALWAYS parseable JSON and a non-zero exit
 * must surface as a structured error carrying that error/kind, never as a JSON
 * parse failure (audit C21). Diagnostics go to stderr and are only used as a
 * last-resort message when stdout genuinely is not JSON.
 */

import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI, ExtensionContext } from "@mariozechner/pi-coding-agent";
import { Type } from "typebox";
import { runJsonCommand, type ExecFn } from "./cli-bridge.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));
// Three levels up from extensions/superrobot/ reaches the repo root, which is
// where the project's own .venv lives (same convention index.ts uses to reach
// vendor/datarobot-agent-skills).
const REPO_ROOT = join(__dirname, "..", "..", "..");

const IR_MODULE = "superrobot.ir";
const IR_LABEL = "superrobot.ir";

// --- response shapes ------------------------------------------------------

export interface Coverage {
  clean: boolean;
  blocking: unknown[];
  unaccounted: unknown[];
  report: string;
}

export interface ExtractResponse {
  ir: unknown;
  coverage: Coverage;
  targetFramework: string;
}

export interface ReportResponse {
  report: string;
  clean: boolean;
}

export interface DecisionsResponse {
  yaml: string;
  path: string;
  blockingCount: number;
}

export interface SpecResponse {
  agentSpec: string;
}

export interface ScaffoldResponse {
  targetDir: string;
  framework: string;
  steps: Array<{ script: string; ok: boolean }>;
}

/**
 * The failure object the Python side writes to stdout. `blocking` is not part
 * of the minimal documented shape -- it is read opportunistically so that a
 * ledger refusal can name the individual blockers when the Python side chooses
 * to include them.
 */
export interface IrErrorPayload {
  error?: string;
  kind?: string;
  blocking?: unknown[];
}

export type IrFailure = {
  ok: false;
  /**
   * - `ir_error`   — the pipeline ran and reported a structured failure
   * - `parse_error`— stdout was not JSON at all (the contract was violated)
   * - `not_found`  — the interpreter or module could not be launched
   */
  reason: "ir_error" | "parse_error" | "not_found";
  message: string;
  kind?: string;
  blocking: unknown[];
};

export type IrResult<T> = { ok: true; data: T } | IrFailure;

// --- interpreter resolution ----------------------------------------------

/**
 * The existing cli-bridge does not resolve a Python interpreter at all -- it
 * execs the `superrobot` console script off PATH. `python -m superrobot.ir`
 * needs an actual interpreter, so resolve one here, preferring (in order) an
 * explicit override, the active virtualenv, the repo's own .venv, and finally
 * whatever `python3` is on PATH.
 */
export function resolvePythonExecutable(env: NodeJS.ProcessEnv = process.env): string {
  if (env.SUPERROBOT_PYTHON) return env.SUPERROBOT_PYTHON;
  if (env.VIRTUAL_ENV) return join(env.VIRTUAL_ENV, "bin", "python");
  const repoVenvPython = join(REPO_ROOT, ".venv", "bin", "python");
  if (existsSync(repoVenvPython)) return repoVenvPython;
  return "python3";
}

// --- bridge ---------------------------------------------------------------

function asErrorPayload(data: unknown): IrErrorPayload {
  return data && typeof data === "object" ? (data as IrErrorPayload) : {};
}

async function runIr<T>(exec: ExecFn, args: string[], cwd?: string): Promise<IrResult<T>> {
  const result = await runJsonCommand<T>(exec, args, { cwd }, IR_LABEL);
  if (result.ok) return { ok: true, data: result.data };
  if (result.reason === "parse_error") {
    return { ok: false, reason: "parse_error", message: result.message, blocking: [] };
  }
  if (result.reason === "not_found") {
    return { ok: false, reason: "not_found", message: result.message, blocking: [] };
  }
  // cli_error: a non-zero exit. Per the contract stdout still holds the
  // {"error","kind"} object, so prefer that over the stderr tail -- stderr is
  // diagnostics and is frequently just a traceback.
  const payload = asErrorPayload(result.data);
  return {
    ok: false,
    reason: "ir_error",
    message: payload.error || result.message,
    kind: payload.kind,
    blocking: payload.blocking ?? [],
  };
}

export function createIrBridge(exec: ExecFn) {
  return {
    extract(repo: string, opts: { decisions?: string } = {}, cwd?: string) {
      const args = ["extract", repo];
      if (opts.decisions) args.push("--decisions", opts.decisions);
      return runIr<ExtractResponse>(exec, args, cwd);
    },

    report(repo: string, opts: { decisions?: string } = {}, cwd?: string) {
      const args = ["report", repo];
      if (opts.decisions) args.push("--decisions", opts.decisions);
      return runIr<ReportResponse>(exec, args, cwd);
    },

    decisionsTemplate(repo: string, cwd?: string) {
      return runIr<DecisionsResponse>(exec, ["decisions-template", repo], cwd);
    },

    spec(repo: string, opts: { decisions?: string } = {}, cwd?: string) {
      const args = ["spec", repo];
      if (opts.decisions) args.push("--decisions", opts.decisions);
      return runIr<SpecResponse>(exec, args, cwd);
    },

    scaffold(targetDir: string, opts: { framework: string; llmModel: string }, cwd?: string) {
      return runIr<ScaffoldResponse>(
        exec,
        ["scaffold", targetDir, "--framework", opts.framework, "--llm-model", opts.llmModel],
        cwd,
      );
    },
  };
}

export type IrBridge = ReturnType<typeof createIrBridge>;

// --- tools ----------------------------------------------------------------

const MAX_LISTED_BLOCKERS = 20;

/** Renders blockers as one line each, tolerating whatever shape the IR emits. */
function formatBlockers(blocking: unknown[]): string {
  const lines = blocking.slice(0, MAX_LISTED_BLOCKERS).map((item) => {
    if (typeof item === "string") return `  - ${item}`;
    const record = (item ?? {}) as Record<string, unknown>;
    const fact = record.fact ?? record.id ?? record.name ?? JSON.stringify(item);
    const reason = record.reason ?? record.message ?? record.why;
    return reason ? `  - ${String(fact)}: ${String(reason)}` : `  - ${String(fact)}`;
  });
  const omitted = blocking.length - lines.length;
  if (omitted > 0) lines.push(`  - ...and ${omitted} more`);
  return lines.join("\n");
}

/**
 * A ledger refusal is a *deliberate* block awaiting a human decision, not a
 * broken tool call, so sr_spec returns it rather than throwing. The Python
 * side signals it through the error `kind`/message; match loosely because the
 * exact class name is the Python side's to choose, and fall through to a hard
 * error for anything that clearly is not a ledger problem.
 */
function isLedgerRefusal(failure: IrFailure): boolean {
  if (failure.reason !== "ir_error") return false;
  if (failure.blocking.length > 0) return true;
  const haystack = `${failure.kind ?? ""} ${failure.message}`.toLowerCase();
  return /ledger|not clean|unclean|coverage|blocking/.test(haystack);
}

function structuredError(tool: string, failure: IrFailure): Error {
  const kind = failure.kind ? ` [${failure.kind}]` : ` [${failure.reason}]`;
  const blockers = failure.blocking.length > 0 ? `\n${formatBlockers(failure.blocking)}` : "";
  return new Error(`${tool} failed${kind}: ${failure.message}${blockers}`);
}

export function registerIrTools(
  pi: ExtensionAPI,
  // Injectable only for tests -- production callers (index.ts) rely on the
  // default so this never changes registerIrTools(pi)'s call sites.
  pythonExecutable: string = resolvePythonExecutable(),
): void {
  const ir = createIrBridge((args, opts) => pi.exec(pythonExecutable, ["-m", IR_MODULE, ...args], opts));

  function notify(ctx: ExtensionContext | undefined, message: string): void {
    try {
      ctx?.ui?.notify(message, "warning");
    } catch {
      // The UI is best-effort: never fail a tool call because a notification
      // could not be shown.
    }
  }

  pi.registerTool({
    name: "sr_extract",
    label: "SuperRobot Extract IR",
    // Every IR tool reads and writes the same on-disk IR/decisions artifacts
    // for a repo, so running two of them concurrently (Pi's default for
    // sibling tool calls in one turn) could interleave writes. Serialize.
    executionMode: "sequential",
    description:
      "Build the Migration IR from a source agent repo: entry points, tools, LLM calls with dataflow-resolved models, orchestration topology, state, external I/O, config and residue. Returns the IR plus the coverage ledger verdict.",
    promptSnippet: "Extract the Migration IR from a brownfield agent repo",
    promptGuidelines: [
      "Use sr_extract first for any migration of an existing agent repo -- every other IR tool reads the IR it produces.",
      "Read the coverage verdict, not just the IR: coverage.clean=false means source behavior is unaccounted for, and the migration cannot honestly proceed until it is resolved.",
      "When coverage is not clean, call sr_report for the full ledger and sr_decisions for the template the human fills in. Do not proceed to sr_spec.",
      "Pass decisions once the user has filled in a decisions file, so previously blocked facts are resolved instead of re-blocking.",
    ],
    parameters: Type.Object({
      repo: Type.String({ description: "Local path to the source agent repo" }),
      decisions: Type.Optional(
        Type.String({ description: "Path to a filled-in decisions file resolving previously blocking facts" }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await ir.extract(params.repo, { decisions: params.decisions });
      if (!result.ok) throw structuredError("sr_extract", result);

      const { coverage, targetFramework } = result.data;
      const blocking = coverage?.blocking ?? [];
      const unaccounted = coverage?.unaccounted ?? [];
      if (coverage?.clean) {
        const text = `IR extracted for ${params.repo}: coverage ledger is clean, target framework ${targetFramework}.`;
        return { content: [{ type: "text", text }], details: result.data };
      }
      notify(ctx, `SuperRobot: coverage ledger not clean (${blocking.length} blocking) -- human decisions required`);
      const text = [
        `IR extracted for ${params.repo}, but the coverage ledger is NOT clean: ` +
          `${blocking.length} blocking, ${unaccounted.length} unaccounted. Target framework ${targetFramework}.`,
        blocking.length > 0 ? `Blocking facts:\n${formatBlockers(blocking)}` : "",
        "This is a deliberate stop, not a tool problem. Call sr_report for the full ledger and sr_decisions " +
          "for a decisions template, then surface the blockers to the user for a decision. Do not call sr_spec yet.",
      ]
        .filter(Boolean)
        .join("\n\n");
      return { content: [{ type: "text", text }], details: result.data };
    },
  });

  pi.registerTool({
    name: "sr_report",
    label: "SuperRobot Coverage Report",
    executionMode: "sequential",
    description:
      "Render the human-readable coverage ledger for a repo: which source facts migrate, which are deferred and why, and which block the migration outright.",
    promptSnippet: "Show the coverage ledger for an extracted repo",
    promptGuidelines: [
      "Use sr_report after sr_extract whenever the coverage verdict is not clean, or whenever the user asks what will and will not carry over.",
      "Show the report text to the user essentially verbatim -- it is the review surface for the migration, and summarizing it away hides exactly the gaps it exists to expose.",
      "A report with blocking entries is a normal, expected result, not a failure to work around.",
    ],
    parameters: Type.Object({
      repo: Type.String({ description: "Local path to the source agent repo" }),
      decisions: Type.Optional(Type.String({ description: "Path to a filled-in decisions file" })),
    }),
    async execute(_toolCallId, params) {
      const result = await ir.report(params.repo, { decisions: params.decisions });
      if (!result.ok) throw structuredError("sr_report", result);
      return { content: [{ type: "text", text: result.data.report }], details: result.data };
    },
  });

  pi.registerTool({
    name: "sr_decisions",
    label: "SuperRobot Decisions Template",
    executionMode: "sequential",
    description:
      "Emit a decisions-file template pre-filled with every blocking fact awaiting a human call. The user fills it in and it is then passed back via the decisions parameter of sr_extract/sr_report/sr_spec.",
    promptSnippet: "Generate a decisions template for the blocking facts",
    promptGuidelines: [
      "Use sr_decisions whenever the coverage ledger is not clean -- it is the intended next step after a blocked sr_extract or a refused sr_spec.",
      "Write the returned yaml to the suggested path, show the blocking entries to the user, and ask them to decide. You must not invent decisions on the user's behalf: a blocker exists precisely because the correct behavior cannot be determined from the code.",
      "After the user fills the file in, re-run sr_extract with decisions set to that path.",
    ],
    parameters: Type.Object({
      repo: Type.String({ description: "Local path to the source agent repo" }),
    }),
    async execute(_toolCallId, params) {
      const result = await ir.decisionsTemplate(params.repo);
      if (!result.ok) throw structuredError("sr_decisions", result);
      const { yaml, path, blockingCount } = result.data;
      const text = `Decisions template (${blockingCount} blocking fact(s)) -- write to ${path}:\n\n${yaml}`;
      return { content: [{ type: "text", text }], details: result.data };
    },
  });

  pi.registerTool({
    name: "sr_spec",
    label: "SuperRobot Agent Spec",
    executionMode: "sequential",
    description:
      "Project the Migration IR into DataRobot's agent_spec.md interchange format. Refuses while the coverage ledger is not clean -- that refusal is a deliberate block awaiting a human decision, not an error.",
    promptSnippet: "Project the IR into DataRobot's agent_spec.md",
    promptGuidelines: [
      "Use sr_spec only once sr_extract reports a clean coverage ledger; it is the step before sr_scaffold.",
      "If sr_spec refuses because the ledger is not clean, that is the architecture working as designed. Do NOT retry it, do not pass different arguments to get around it, and do not hand-write an agent_spec.md yourself. Call sr_decisions, surface the blocking facts to the user, and wait for their decision.",
      "Emitting a spec that silently drops blocked behavior would produce an agent that looks plausible and behaves differently -- the one outcome this tool exists to prevent.",
    ],
    parameters: Type.Object({
      repo: Type.String({ description: "Local path to the source agent repo" }),
      decisions: Type.Optional(Type.String({ description: "Path to a filled-in decisions file" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await ir.spec(params.repo, { decisions: params.decisions });
      if (result.ok) {
        return { content: [{ type: "text", text: result.data.agentSpec }], details: result.data };
      }
      if (!isLedgerRefusal(result)) throw structuredError("sr_spec", result);

      notify(ctx, "SuperRobot: sr_spec refused -- coverage ledger is not clean, human decisions required");
      const text = [
        `sr_spec refused to emit an agent_spec.md for ${params.repo}: ${result.message}`,
        result.blocking.length > 0 ? `Blocking facts:\n${formatBlockers(result.blocking)}` : "",
        "This refusal is deliberate. A human decision is required. Call sr_decisions to get the template, " +
          "present the blocking facts above to the user, and re-run sr_extract with their filled-in decisions file. " +
          "Do not retry sr_spec until the ledger is clean, and do not write an agent_spec.md by hand.",
      ]
        .filter(Boolean)
        .join("\n\n");
      return {
        content: [{ type: "text", text }],
        details: { refused: true, kind: result.kind, error: result.message, blocking: result.blocking },
      };
    },
  });

  pi.registerTool({
    name: "sr_scaffold",
    label: "SuperRobot Scaffold",
    executionMode: "sequential",
    description:
      "Run DataRobot's own clone_template / select_framework / setup_template scripts into a target directory, producing the authoritative agent recipe (including .datarobot/) rather than a hand-rolled copy of it.",
    promptSnippet: "Scaffold the DataRobot agent recipe into a target directory",
    promptGuidelines: [
      "Use sr_scaffold after sr_spec succeeds. Take framework from the IR's orchestration topology (targetFramework returned by sr_extract), not from a guess at the source repo's imports.",
      "Pick llmModel from a model the user's DataRobot account actually has; ask the user if it is not already established.",
      "If a step reports ok=false, report which script failed to the user -- these are DataRobot's own scripts and the failure is environmental (auth, network, pinned template version), not something to re-implement by hand.",
    ],
    parameters: Type.Object({
      targetDir: Type.String({ description: "Directory to scaffold the DataRobot agent recipe into" }),
      framework: Type.String({ description: "Target framework, e.g. the targetFramework returned by sr_extract" }),
      llmModel: Type.String({ description: "LLM model id to configure the scaffolded agent with" }),
    }),
    async execute(_toolCallId, params) {
      const result = await ir.scaffold(params.targetDir, {
        framework: params.framework,
        llmModel: params.llmModel,
      });
      if (!result.ok) throw structuredError("sr_scaffold", result);
      const steps = result.data.steps ?? [];
      const failed = steps.filter((step) => !step.ok);
      const text = [
        `Scaffolded ${result.data.framework} recipe into ${result.data.targetDir}.`,
        ...steps.map((step) => `  ${step.ok ? "ok" : "FAILED"}  ${step.script}`),
        failed.length > 0 ? `${failed.length} step(s) failed -- the scaffold is incomplete.` : "",
      ]
        .filter(Boolean)
        .join("\n");
      return { content: [{ type: "text", text }], details: result.data };
    },
  });
}
