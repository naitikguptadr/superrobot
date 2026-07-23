/**
 * SuperRobot interactive shell — deep Pi customization entrypoint.
 *
 * Branding and Gateway wiring happen two ways:
 * - Here: endpoint/token/model resolution, spawning `pi` with `-e` (our extension,
 *   see ../extensions/superrobot.ts) and `--system-prompt` — both real, documented
 *   pi CLI mechanisms (checked against node_modules/@mariozechner/pi-coding-agent's
 *   own docs, not guessed).
 * - In the extension: provider registration, theme selection, capability chips.
 *   Pi does not read env vars for base URL / API key / theme / system prompt, so
 *   those are no longer set here — only `-e`, `--system-prompt`, and the real env
 *   vars our own extension consumes (SUPERROBOT_GATEWAY_BASE_URL, SUPERROBOT_MODEL,
 *   DATAROBOT_API_TOKEN) are passed.
 * The Python `superrobot` CLI remains the engine / setup surface.
 */

import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const extensionPath = join(__dirname, "..", "extensions", "superrobot", "index.ts");
const systemPromptPath = join(__dirname, "..", "prompts", "system.md");

const DEFAULT_SYSTEM_PROMPT = "You are SuperRobot, a DataRobot brownfield deployment specialist.";

function loadUserEnv(): Record<string, string> {
  const envPath = join(homedir(), ".config", "superrobot", ".env");
  if (!existsSync(envPath)) return {};
  const values: Record<string, string> = {};
  for (const line of readFileSync(envPath, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const idx = trimmed.indexOf("=");
    values[trimmed.slice(0, idx)] = trimmed.slice(idx + 1);
  }
  return values;
}

interface GatewayConfig {
  endpoint: string;
  token: string;
  model: string;
  gatewayBaseUrl: string;
}

function resolveGatewayConfig(base: Record<string, string>): GatewayConfig {
  const endpoint = (process.env.DATAROBOT_ENDPOINT || base.DATAROBOT_ENDPOINT || "").replace(/\/$/, "");
  const token = process.env.DATAROBOT_API_TOKEN || base.DATAROBOT_API_TOKEN || "";
  const model = process.env.SUPERROBOT_MODEL || base.SUPERROBOT_MODEL || "azure/gpt-5-5-2026-04-23";
  const apiRoot = endpoint.endsWith("/api/v2") ? endpoint : `${endpoint}/api/v2`;
  const gatewayBaseUrl = endpoint ? `${apiRoot}/genai/llmgw/v1` : "";
  return { endpoint, token, model, gatewayBaseUrl };
}

function printBanner(): void {
  const lines = [
    "",
    "  ███████╗██╗   ██╗██████╗ ███████╗██████╗ ██████╗  ██████╗ ██████╗  ██████╗ ████████╗",
    "  ██╔════╝██║   ██║██╔══██╗██╔════╝██╔══██╗██╔══██╗██╔═══██╗██╔══██╗██╔═══██╗╚══██╔══╝",
    "  ███████╗██║   ██║██████╔╝█████╗  ██████╔╝██████╔╝██║   ██║██████╔╝██║   ██║   ██║   ",
    "  ╚════██║██║   ██║██╔═══╝ ██╔══╝  ██╔══██╗██╔══██╗██║   ██║██╔══██╗██║   ██║   ██║   ",
    "  ███████║╚██████╔╝██║     ███████╗██║  ██║██║  ██║╚██████╔╝██████╔╝╚██████╔╝   ██║   ",
    "  ╚══════╝ ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝  ╚═════╝    ╚═╝   ",
    "",
    "  DataRobot-native brownfield control plane",
    "  Models → LLM Gateway · Deploy → Agent App / Workload · Memory when entitled",
    "",
  ];
  process.stderr.write(lines.join("\n") + "\n");
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const quiet = args.includes("-p") || args.includes("--print") || args.includes("--json");
  if (!quiet) printBanner();

  const userEnv = loadUserEnv();
  const { endpoint, token, model, gatewayBaseUrl } = resolveGatewayConfig(userEnv);
  if (!token || !endpoint) {
    process.stderr.write(
      "SuperRobot: not authenticated. Run `superrobot setup` (Python CLI) first.\n",
    );
    process.exit(2);
  }

  const env: NodeJS.ProcessEnv = {
    ...process.env,
    ...userEnv,
    DATAROBOT_ENDPOINT: endpoint,
    DATAROBOT_API_TOKEN: token,
    SUPERROBOT_MODEL: model,
    SUPERROBOT_GATEWAY_BASE_URL: gatewayBaseUrl,
  };

  const systemPrompt = existsSync(systemPromptPath)
    ? readFileSync(systemPromptPath, "utf8")
    : DEFAULT_SYSTEM_PROMPT;

  // Prefer the installed Pi binary; fall back to npx.
  const child = spawn(
    "npx",
    [
      "--yes",
      "@mariozechner/pi-coding-agent",
      "-e",
      extensionPath,
      "--system-prompt",
      systemPrompt,
      ...args,
    ],
    {
      stdio: "inherit",
      env,
      shell: process.platform === "win32",
    },
  );

  child.on("exit", (code) => process.exit(code ?? 1));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
