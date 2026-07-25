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
import { createWebController, type WebController } from "./web-controller.ts";

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

export function registerSuperRobotTools(
  pi: ExtensionAPI,
  // Injectable only for tests -- production callers (index.ts) rely on the
  // default so this never changes registerSuperRobotTools(pi)'s call sites.
  webControllerFactory: typeof createWebController = createWebController,
): void {
  const cli = createCliBridge((args, opts) => pi.exec("superrobot", args, opts));

  let pipeline: PipelineState = freshPipeline();
  let rail: RailController | undefined;
  let web: WebController | undefined;
  let hasNotifiedWebUrl = false;

  // `isNewRail` tells callers whether `rail` had to be created by this very
  // call (i.e. this is the first pipeline tool call of the session). Only
  // superrobot_scan used to ever call rc.start() -- if a fresh session's
  // first pipeline tool call was transform/validate/deploy/receipts instead,
  // rc.start() (which arms the rail's ~90ms spinner-animation interval) was
  // never called and the spinner glyph stayed visually frozen. Every handler
  // below uses `isNewRail` to call rc.start() instead of rc.update() exactly
  // once, the first time it sees a rail it just created.
  function railFor(ctx: ExtensionContext): { rc: RailController; isNewRail: boolean } {
    const isNewRail = !rail;
    if (!rail) rail = createRailController(ctx);
    return { rc: rail, isNewRail };
  }

  // The companion web UI is strictly optional and additive: any failure here
  // (constructing the controller, starting its server, or pushing an update)
  // must never fail the actual superrobot_* tool call. webFor() itself isn't
  // expected to throw (createWebController just builds an object -- it does
  // no I/O), but we guard it anyway since a future change could make it
  // fallible and we'd rather swallow that than break the pipeline tools.
  function webFor(): WebController | undefined {
    if (web) return web;
    try {
      web = webControllerFactory({ port: 0 });
      return web;
    } catch (err) {
      console.error("[superrobot] failed to create web companion controller:", err);
      return undefined;
    }
  }

  async function safeWebStart(wc: WebController | undefined, state: PipelineState, ctx: ExtensionContext): Promise<void> {
    if (!wc) return;
    try {
      const { port } = await wc.start(state);
      // Only announce the URL once per session: the port never changes once
      // bound, so re-notifying on every subsequent pipeline tool call (scan,
      // transform, validate, deploy, receipts) would just be noise.
      if (!hasNotifiedWebUrl && port > 0) {
        hasNotifiedWebUrl = true;
        ctx.ui.notify(`SuperRobot companion UI: http://localhost:${port}`);
      }
    } catch (err) {
      console.error("[superrobot] web companion failed to start:", err);
    }
  }

  function safeWebUpdate(wc: WebController | undefined, state: PipelineState): void {
    if (!wc) return;
    try {
      wc.update(state);
    } catch (err) {
      console.error("[superrobot] web companion failed to update:", err);
    }
  }

  pi.registerTool({
    name: "superrobot_scan",
    label: "SuperRobot Scan",
    // All five pipeline-stage tools share a single `pipeline`/`rail` closure
    // per session. Pi runs sibling tool calls from the same turn concurrently
    // by default; forcing these to run one at a time prevents two in-flight
    // calls from interleaving writes to that shared state (e.g. a second
    // superrobot_scan's freshPipeline() wiping out a still-running call's
    // progress).
    executionMode: "sequential",
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
      const { rc, isNewRail } = railFor(ctx);
      const wc = webFor();
      pipeline = freshPipeline();
      pipeline = withStageActive(pipeline, "scan", params.path);
      if (isNewRail) rc.start(pipeline); else rc.update(pipeline);
      await safeWebStart(wc, pipeline, ctx);

      const result = await cli.scan(params.path);
      if (!result.ok) {
        pipeline = withStageFailed(pipeline, "scan", result.message);
        rc.update(pipeline);
        safeWebUpdate(wc, pipeline);
        throw new Error(`superrobot scan failed: ${result.message}`);
      }
      const data = result.data as ScanResult;
      const detail = `${data.detected_framework} detected, ${(data.env_vars ?? []).length} env vars, conf ${data.confidence.toFixed(2)}`;
      pipeline = withStageDone(pipeline, "scan", detail);
      rc.update(pipeline);
      safeWebUpdate(wc, pipeline);
      return { content: [{ type: "text", text: detail }], details: data };
    },
  });

  pi.registerTool({
    name: "superrobot_transform",
    label: "SuperRobot Transform",
    executionMode: "sequential",
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
      const { rc, isNewRail } = railFor(ctx);
      const wc = webFor();
      pipeline = withStageActive(pipeline, "transform", params.outputDir);
      if (isNewRail) rc.start(pipeline); else rc.update(pipeline);
      safeWebUpdate(wc, pipeline);

      const result = await cli.transform(params.path, {
        outputDir: params.outputDir,
        skipEval: params.skipEval,
      });
      if (!result.ok) {
        pipeline = withStageFailed(pipeline, "transform", result.message);
        rc.update(pipeline);
        safeWebUpdate(wc, pipeline);
        throw new Error(`superrobot transform failed: ${result.message}`);
      }
      const data = result.data as { files?: string[] };
      const detail = `${(data.files ?? []).length} files generated`;
      pipeline = withStageDone(pipeline, "transform", detail);
      rc.update(pipeline);
      safeWebUpdate(wc, pipeline);
      return { content: [{ type: "text", text: detail }], details: data };
    },
  });

  pi.registerTool({
    name: "superrobot_validate",
    label: "SuperRobot Validate",
    executionMode: "sequential",
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
      const { rc, isNewRail } = railFor(ctx);
      const wc = webFor();
      pipeline = withStageActive(pipeline, "validate", params.dir);
      if (isNewRail) rc.start(pipeline); else rc.update(pipeline);
      safeWebUpdate(wc, pipeline);

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
        safeWebUpdate(wc, pipeline);
        throw new Error(`superrobot validate failed: ${result.message}`);
      }
      const findings = data?.findings ?? [];
      const blocking = findings.filter((f) => f.severity === "blocking").length;
      const warnings = findings.filter((f) => f.severity === "warning").length;
      const detail = blocking > 0 ? `${blocking} blocking, ${warnings} warning(s)` : `clean (${warnings} warning(s))`;
      pipeline = blocking > 0 ? withStageFailed(pipeline, "validate", detail) : withStageDone(pipeline, "validate", detail);
      rc.update(pipeline);
      safeWebUpdate(wc, pipeline);
      return { content: [{ type: "text", text: detail }], details: data ?? {} };
    },
  });

  pi.registerTool({
    name: "superrobot_deploy",
    label: "SuperRobot Deploy",
    executionMode: "sequential",
    description:
      "Deploy a generated package to Agent App or Workload API. Always confirms with the user before running, and surfaces the known BUZZOK-30076 build-time and logs-deleted-on-failure warnings.",
    promptSnippet: "Deploy a validated package to Agent App or Workload",
    promptGuidelines: [
      "Use superrobot_deploy only after superrobot_validate reports zero blocking findings.",
      "Never set waive on superrobot_deploy unless the user explicitly asked to waive or override a specific Gap Analysis finding.",
      "For target=workload, use artifactId (not imageUri) when the user already has a Code-to-Workload (server-side build) artifact -- those images live in DataRobot's own internal registry and are rejected as 'not permitted on this cluster' if you instead build a fresh artifact from imageUri. Ask the user which one they have if unclear.",
    ],
    parameters: Type.Object({
      dir: Type.String({ description: "Generated package directory" }),
      target: StringEnum(["agent-app", "workload"] as const),
      imageUri: Type.Optional(Type.String({ description: "Built container image URI (bring-your-own-image), for target=workload" })),
      artifactId: Type.Optional(Type.String({ description: "Existing Workload API artifact id (e.g. from a Code-to-Workload build), for target=workload. Use instead of imageUri, never both." })),
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

      const { rc, isNewRail } = railFor(ctx);
      const wc = webFor();
      pipeline = withStageActive(pipeline, "deploy", params.target);
      if (isNewRail) rc.start(pipeline); else rc.update(pipeline);
      safeWebUpdate(wc, pipeline);

      const result = await cli.deploy(params.dir, params.target, {
        imageUri: params.imageUri,
        artifactId: params.artifactId,
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
        safeWebUpdate(wc, pipeline);
        throw new Error(`superrobot deploy failed: ${result.message}`);
      }
      const succeeded = data?.success ?? false;
      const detail = succeeded ? "deployed" : (data?.error_message ?? "blocked or failed");
      pipeline = succeeded ? withStageDone(pipeline, "deploy", detail) : withStageFailed(pipeline, "deploy", detail);
      rc.update(pipeline);
      safeWebUpdate(wc, pipeline);
      return { content: [{ type: "text", text: detail }], details: data ?? {} };
    },
  });

  pi.registerTool({
    name: "superrobot_receipts",
    label: "SuperRobot Receipts",
    executionMode: "sequential",
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
      const { rc, isNewRail } = railFor(ctx);
      const wc = webFor();
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
        if (params.action === "show" || params.action === "replace") {
          pipeline = withStageFailed(pipeline, "receipt", result.message);
        }
        // Arm/refresh the rail and web companion on every action, not just
        // show/replace: railFor(ctx) above unconditionally creates `rail`
        // (setting isNewRail) as a side effect, so every action must call
        // rc.start()/rc.update() here or a rail created by an
        // operations/diagnose call would never get its spinner interval
        // armed -- and every later call in the session would then see
        // isNewRail=false and keep calling rc.update() instead, leaving the
        // spinner frozen for the rest of the session.
        if (isNewRail) rc.start(pipeline); else rc.update(pipeline);
        safeWebUpdate(wc, pipeline);
        throw new Error(`superrobot receipt ${params.action} failed: ${result.message}`);
      }
      if (params.action === "show" || params.action === "replace") {
        pipeline = withStageDone(pipeline, "receipt", params.action);
      }
      // Same reasoning as the failure branch above: arm/refresh the rail for
      // every action so operations/diagnose can't leave it un-started.
      if (isNewRail) rc.start(pipeline); else rc.update(pipeline);
      safeWebUpdate(wc, pipeline);
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

  // rail/web are designed to persist for the whole shell session (so status
  // survives across multiple pipeline tool calls), but nothing calls .stop()
  // on them today -- they rely on the whole Node process exiting to
  // implicitly release the rail's setInterval and the web server's HTTP+WS
  // listeners. That's wasteful when a session ends via Pi's own
  // session-switching (fork/resume/new) without the process itself exiting.
  // session_shutdown fires in exactly that case, so use it to release both
  // resources and reset the singletons back to their initial unset state --
  // this closure could in principle be reused by a fresh session, and even if
  // that's not how Pi actually works today, recreating fresh controllers
  // rather than reusing stale, stopped ones is the defensively correct thing
  // to do.
  pi.on("session_shutdown", async () => {
    rail?.stop();
    if (web) {
      try {
        await web.stop();
      } catch (err) {
        console.error("[superrobot] web companion failed to stop:", err);
      }
    }
    rail = undefined;
    web = undefined;
    hasNotifiedWebUrl = false;
    pipeline = freshPipeline();
  });
}
