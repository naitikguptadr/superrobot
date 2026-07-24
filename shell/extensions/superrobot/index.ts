/**
 * SuperRobot Pi extension — DataRobot Gateway provider, theme, capability chips.
 *
 * Loaded via `-e` by shell/src/cli.ts. Runs inside the same Node process as the
 * `pi` interactive shell, so it sees the env vars cli.ts sets on the child process
 * (DATAROBOT_API_TOKEN, SUPERROBOT_MODEL, SUPERROBOT_GATEWAY_BASE_URL) and reads
 * `~/.config/superrobot/setup.json` directly for capability chips — no dependency
 * on the `superrobot` Python binary being on PATH at runtime.
 */

import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { registerSuperRobotTools } from "./tools.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));
// index.ts now lives at extensions/superrobot/ (moved there in the directory
// restructure) -- two levels up reaches shell/, then theme/.
const THEME_DIR = join(__dirname, "..", "..", "theme");
// Three levels up from extensions/superrobot/ reaches the repo root, then
// the vendored datarobot-agent-skills submodule's skills/ directory.
const DATAROBOT_SKILLS_DIR = join(__dirname, "..", "..", "..", "vendor", "datarobot-agent-skills", "skills");
const PROVIDER_NAME = "datarobot-gateway";

interface Capabilities {
  llm_gateway: boolean;
  agent_app: boolean;
  workload: boolean;
  memory: boolean;
}

function loadCapabilities(): Capabilities | null {
  const configDir = process.env.SUPERROBOT_CONFIG_DIR || join(homedir(), ".config", "superrobot");
  const statePath = join(configDir, "setup.json");
  if (!existsSync(statePath)) return null;
  try {
    const parsed = JSON.parse(readFileSync(statePath, "utf8")) as {
      capabilities?: Capabilities;
    };
    return parsed.capabilities ?? null;
  } catch {
    return null;
  }
}

function chipLine(caps: Capabilities | null): string {
  if (!caps) return "SuperRobot — run `superrobot setup` for capability chips";
  const chip = (label: string, on: boolean) => (on ? `●${label}` : `○${label}`);
  return [
    chip(" Gateway", caps.llm_gateway),
    chip(" Agent App", caps.agent_app),
    chip(" Workload", caps.workload),
    chip(" Memory", caps.memory),
  ].join("  ");
}

export default function (pi: ExtensionAPI) {
  const gatewayBaseUrl = process.env.SUPERROBOT_GATEWAY_BASE_URL || "";
  const model = process.env.SUPERROBOT_MODEL || "azure/gpt-5-5-2026-04-23";

  registerSuperRobotTools(pi);

  if (gatewayBaseUrl) {
    pi.registerProvider(PROVIDER_NAME, {
      name: "DataRobot LLM Gateway",
      baseUrl: gatewayBaseUrl,
      apiKey: "DATAROBOT_API_TOKEN",
      api: "openai-completions",
      models: [
        {
          id: model,
          name: model,
          reasoning: false,
          input: ["text", "image"],
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          contextWindow: 128000,
          maxTokens: 8192,
        },
      ],
    });
  }

  pi.on("resources_discover", async () => ({
    themePaths: [THEME_DIR],
    // Vendored as a git submodule (vendor/datarobot-agent-skills) -- may not
    // be initialized on a fresh clone (`git submodule update --init`), so
    // only contribute the path if it's actually present.
    skillPaths: existsSync(DATAROBOT_SKILLS_DIR) ? [DATAROBOT_SKILLS_DIR] : [],
  }));

  pi.on("session_start", async (_event, ctx) => {
    const themeResult = ctx.ui.setTheme("superrobot");
    if (!themeResult.success) {
      ctx.ui.notify(`SuperRobot theme unavailable: ${themeResult.error}`, "warning");
    }

    ctx.ui.setStatus("superrobot-caps", chipLine(loadCapabilities()));

    if (gatewayBaseUrl) {
      const gatewayModel = ctx.modelRegistry.find(PROVIDER_NAME, model);
      if (gatewayModel) {
        const ok = await pi.setModel(gatewayModel);
        if (!ok) {
          ctx.ui.notify("SuperRobot: DataRobot Gateway model unavailable (check DATAROBOT_API_TOKEN)", "error");
        }
      }
    }
  });
}
