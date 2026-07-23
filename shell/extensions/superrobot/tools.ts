import { StringEnum } from "@mariozechner/pi-ai";
import type { ExtensionAPI, ExtensionContext } from "@mariozechner/pi-coding-agent";
import { Type } from "typebox";
import { createCliBridge, type CliResult } from "./cli-bridge.ts";
import {
  freshPipeline,
  withStageActive,
  withStageDone,
  withStageFailed,
  type PipelineState,
} from "./pipeline-state.ts";
import { createRailController, type RailController } from "./rail-widget.ts";

interface ScanResult {
  detected_framework: string;
  confidence: number;
  env_vars?: string[];
}

interface DeployResult {
  success: boolean;
  error_message?: string;
  blocked_by_gap_analysis?: boolean;
}

interface GapFinding {
  rule: string;
  severity: "blocking" | "warning";
  message: string;
  file?: string | null;
}

interface GapReport {
  findings: GapFinding[];
}

export function registerSuperRobotTools(pi: ExtensionAPI): void {
  const cli = createCliBridge((args, opts) => pi.exec("superrobot", args, opts));

  let pipeline: PipelineState = freshPipeline();
  let rail: RailController | undefined;

  function railFor(ctx: ExtensionContext): RailController {
    if (!rail) rail = createRailController(ctx);
    return rail;
  }

  pi.registerTool({
    name: "superrobot_scan",
    label: "SuperRobot Scan",
    description:
      "Statically scan a Python agent repo (local path) for framework, entry points, env vars, and risk flags. Always the first step when importing a brownfield agent.",
    promptSnippet: "Scan a brownfield agent repo before transforming it",
    promptGuidelines: [
      "Use superrobot_scan first whenever the user asks to import, migrate, or bring an existing agent repo to DataRobot.",
    ],
    parameters: Type.Object({
      path: Type.String({ description: "Local path to the agent repo" }),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const rc = railFor(ctx);
      pipeline = freshPipeline();
      pipeline = withStageActive(pipeline, "scan", params.path);
      rc.start(pipeline);

      const result = await cli.scan(params.path);
      if (!result.ok) {
        pipeline = withStageFailed(pipeline, "scan", result.message);
        rc.update(pipeline);
        throw new Error(`superrobot scan failed: ${result.message}`);
      }
      const data = result.data as ScanResult;
      const detail = `${data.detected_framework} detected, ${(data.env_vars ?? []).length} env vars, conf ${data.confidence.toFixed(2)}`;
      pipeline = withStageDone(pipeline, "scan", detail);
      rc.update(pipeline);
      return { content: [{ type: "text", text: detail }], details: data };
    },
  });

  pi.registerTool({
    name: "superrobot_transform",
    label: "SuperRobot Transform",
    description:
      "Run Scan -> Analyze -> Generate for a brownfield agent repo, writing a DR-compliant package to outputDir.",
    promptSnippet: "Generate a DataRobot-compliant package from a scanned repo",
    promptGuidelines: [
      "Use superrobot_transform after superrobot_scan succeeds, before superrobot_validate.",
    ],
    parameters: Type.Object({
      path: Type.String({ description: "Local path to the agent repo" }),
      outputDir: Type.String({ description: "Directory to write the generated package into" }),
      skipEval: Type.Optional(Type.Boolean({ description: "Skip the 5-shot eval stage" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const rc = railFor(ctx);
      pipeline = withStageActive(pipeline, "transform", params.outputDir);
      rc.update(pipeline);

      const result = await cli.transform(params.path, {
        outputDir: params.outputDir,
        skipEval: params.skipEval,
      });
      if (!result.ok) {
        pipeline = withStageFailed(pipeline, "transform", result.message);
        rc.update(pipeline);
        throw new Error(`superrobot transform failed: ${result.message}`);
      }
      const data = result.data as { files?: string[] };
      const detail = `${(data.files ?? []).length} files generated`;
      pipeline = withStageDone(pipeline, "transform", detail);
      rc.update(pipeline);
      return { content: [{ type: "text", text: detail }], details: data };
    },
  });

  pi.registerTool({
    name: "superrobot_validate",
    label: "SuperRobot Validate",
    description:
      "Run Gap Analysis against a generated package. Reports blocking and warning findings. A non-zero exit with findings is a normal result, not a tool failure.",
    promptSnippet: "Run Gap Analysis on a generated package before deploying",
    promptGuidelines: [
      "Use superrobot_validate after superrobot_transform and before superrobot_deploy.",
      "If superrobot_validate reports blocking findings, report them to the user and do not call superrobot_deploy unless the user explicitly asks to waive a specific finding.",
    ],
    parameters: Type.Object({
      dir: Type.String({ description: "Generated package directory" }),
      source: Type.Optional(Type.String({ description: "Original repo path, enables the pyproject-removal check" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const rc = railFor(ctx);
      pipeline = withStageActive(pipeline, "validate", params.dir);
      rc.update(pipeline);

      const result = await cli.validate(params.dir, params.source);
      // validate exits non-zero when there are blocking findings -- that's a
      // real GapReport, not a broken CLI call. Narrow explicitly: only
      // not_found/parse_error are genuine tool failures; cli_error carries a
      // GapReport we still want to read.
      let data: GapReport | undefined;
      if (result.ok) {
        data = result.data as GapReport;
      } else if (result.reason === "cli_error") {
        data = result.data as GapReport | undefined;
      } else {
        pipeline = withStageFailed(pipeline, "validate", result.message);
        rc.update(pipeline);
        throw new Error(`superrobot validate failed: ${result.message}`);
      }
      const findings = data?.findings ?? [];
      const blocking = findings.filter((f) => f.severity === "blocking").length;
      const warnings = findings.filter((f) => f.severity === "warning").length;
      const detail = blocking > 0 ? `${blocking} blocking, ${warnings} warning(s)` : `clean (${warnings} warning(s))`;
      pipeline = blocking > 0 ? withStageFailed(pipeline, "validate", detail) : withStageDone(pipeline, "validate", detail);
      rc.update(pipeline);
      return { content: [{ type: "text", text: detail }], details: data ?? {} };
    },
  });

  pi.registerTool({
    name: "superrobot_deploy",
    label: "SuperRobot Deploy",
    description:
      "Deploy a generated package to Agent App or Workload API. Always confirms with the user before running, and surfaces the known BUZZOK-30076 build-time and logs-deleted-on-failure warnings.",
    promptSnippet: "Deploy a validated package to Agent App or Workload",
    promptGuidelines: [
      "Use superrobot_deploy only after superrobot_validate reports zero blocking findings.",
      "Never set waive on superrobot_deploy unless the user explicitly asked to waive or override a specific Gap Analysis finding.",
    ],
    parameters: Type.Object({
      dir: Type.String({ description: "Generated package directory" }),
      target: StringEnum(["agent-app", "workload"] as const),
      imageUri: Type.Optional(Type.String({ description: "Built container image URI, required for target=workload" })),
      waive: Type.Optional(Type.Boolean({ description: "Only true if the user explicitly asked to waive a blocking Gap Analysis finding" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const confirmed = await ctx.ui.confirm(
        `Deploy -> ${params.target}`,
        `Deploy ${params.dir} to ${params.target}. This build takes 15-20 minutes (BUZZOK-30076), ` +
          "and Pulumi deletes logs automatically if the deploy fails. Proceed?",
      );
      if (!confirmed) {
        return { content: [{ type: "text", text: "Deploy cancelled by user; no receipt was written." }], details: { cancelled: true } };
      }

      const rc = railFor(ctx);
      pipeline = withStageActive(pipeline, "deploy", params.target);
      rc.update(pipeline);

      const result = await cli.deploy(params.dir, params.target, {
        imageUri: params.imageUri,
        waive: params.waive,
      });
      // Same narrowing as superrobot_validate: only not_found/parse_error are
      // genuine tool failures; cli_error (e.g. Gap-Analysis-blocked deploy)
      // carries a DeployResult we still want to read.
      let data: DeployResult | undefined;
      if (result.ok) {
        data = result.data as DeployResult;
      } else if (result.reason === "cli_error") {
        data = result.data as DeployResult | undefined;
      } else {
        pipeline = withStageFailed(pipeline, "deploy", result.message);
        rc.update(pipeline);
        throw new Error(`superrobot deploy failed: ${result.message}`);
      }
      const succeeded = data?.success ?? false;
      const detail = succeeded ? "deployed" : (data?.error_message ?? "blocked or failed");
      pipeline = succeeded ? withStageDone(pipeline, "deploy", detail) : withStageFailed(pipeline, "deploy", detail);
      rc.update(pipeline);
      return { content: [{ type: "text", text: detail }], details: data ?? {} };
    },
  });

  pi.registerTool({
    name: "superrobot_receipts",
    label: "SuperRobot Receipts",
    description: "Read deploy receipt history: show a receipt, list operations, diagnose a failure, or replace a prior deploy.",
    promptSnippet: "Inspect or act on deploy receipt history",
    promptGuidelines: [
      "Use superrobot_receipts with action=show right after a deploy to confirm a receipt was written.",
    ],
    parameters: Type.Object({
      action: StringEnum(["show", "operations", "diagnose", "replace"] as const),
      id: Type.Optional(Type.String({ description: "Receipt id, required for diagnose/replace, optional for show" })),
      target: Type.Optional(Type.String({ description: "Filter by target for action=operations" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const rc = railFor(ctx);
      let result: CliResult<unknown>;
      switch (params.action) {
        case "show":
          result = await cli.receiptShow(params.id);
          break;
        case "operations":
          result = await cli.receiptOperations(params.target);
          break;
        case "diagnose":
          if (!params.id) throw new Error("action=diagnose requires id");
          result = await cli.receiptDiagnose(params.id);
          break;
        case "replace":
          if (!params.id) throw new Error("action=replace requires id");
          result = await cli.receiptReplace(params.id);
          break;
        default:
          throw new Error(`unknown receipts action: ${String(params.action)}`);
      }
      if (!result.ok) {
        throw new Error(`superrobot receipt ${params.action} failed: ${result.message}`);
      }
      if (params.action === "show" || params.action === "replace") {
        pipeline = withStageDone(pipeline, "receipt", params.action);
        rc.update(pipeline);
      }
      return { content: [{ type: "text", text: JSON.stringify(result.data) }], details: result.data as object };
    },
  });

  pi.registerTool({
    name: "superrobot_memory_ensure",
    label: "SuperRobot Memory Ensure",
    description: "Idempotently ensure a named DataRobot Memory API space exists.",
    promptSnippet: "Ensure a Memory API space exists for an agent",
    promptGuidelines: [
      "Use superrobot_memory_ensure only when the user's agent needs persistent memory and the Memory capability chip is on.",
    ],
    parameters: Type.Object({
      name: Type.String({ description: "Memory space name" }),
    }),
    async execute(_toolCallId, params) {
      const result = await cli.memoryEnsure(params.name);
      if (!result.ok) {
        throw new Error(`superrobot memory ensure failed: ${result.message}`);
      }
      return { content: [{ type: "text", text: JSON.stringify(result.data) }], details: result.data as object };
    },
  });
}
