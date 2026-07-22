/**
 * SuperRobot interactive shell — deep Pi customization entrypoint.
 *
 * Branding, Gateway-only provider wiring, and theme live here.
 * The Python `superrobot` CLI remains the engine / setup surface.
 */

import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const themePath = join(__dirname, "..", "theme", "superrobot.theme.json");
const systemPromptPath = join(__dirname, "..", "prompts", "system.md");

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

function gatewayEnv(base: Record<string, string>): NodeJS.ProcessEnv {
  const endpoint = (process.env.DATAROBOT_ENDPOINT || base.DATAROBOT_ENDPOINT || "").replace(
    /\/$/,
    "",
  );
  const token = process.env.DATAROBOT_API_TOKEN || base.DATAROBOT_API_TOKEN || "";
  const model = process.env.SUPERROBOT_MODEL || base.SUPERROBOT_MODEL || "azure/gpt-5-5-2026-04-23";
  const apiRoot = endpoint.endsWith("/api/v2") ? endpoint : `${endpoint}/api/v2`;
  const gateway = `${apiRoot}/genai/llmgw/v1`;

  return {
    ...process.env,
    ...base,
    OPENAI_BASE_URL: gateway,
    OPENAI_API_KEY: token,
    OPENAI_MODEL: model,
    PI_THEME: themePath,
    SUPERROBOT_SYSTEM_PROMPT: existsSync(systemPromptPath)
      ? readFileSync(systemPromptPath, "utf8")
      : "You are SuperRobot, a DataRobot brownfield deployment specialist.",
    SUPERROBOT_BRAND: "1",
  };
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
  const env = gatewayEnv(userEnv);
  if (!env.DATAROBOT_API_TOKEN && !env.OPENAI_API_KEY) {
    process.stderr.write(
      "SuperRobot: not authenticated. Run `superrobot setup` (Python CLI) first.\n",
    );
    process.exit(2);
  }

  // Prefer the installed Pi binary; fall back to npx.
  const child = spawn("npx", ["--yes", "@mariozechner/pi-coding-agent", ...args], {
    stdio: "inherit",
    env,
    shell: process.platform === "win32",
  });

  child.on("exit", (code) => process.exit(code ?? 1));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
