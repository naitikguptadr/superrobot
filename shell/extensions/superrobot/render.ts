import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
// extensions/superrobot/render.ts -> up two levels to shell/, then theme/
const THEME_PATH = join(__dirname, "..", "..", "theme", "superrobot.theme.json");

export type RailColorName = "teal" | "tealMuted" | "gold" | "green" | "red" | "slate";

function loadThemeVars(): Record<string, string> {
  const parsed = JSON.parse(readFileSync(THEME_PATH, "utf8")) as { vars: Record<string, string> };
  return parsed.vars;
}

const THEME_VARS = loadThemeVars();

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace("#", "");
  return [
    parseInt(clean.slice(0, 2), 16),
    parseInt(clean.slice(2, 4), 16),
    parseInt(clean.slice(4, 6), 16),
  ];
}

export function railColor(name: RailColorName, text: string): string {
  const hex = THEME_VARS[name];
  if (!hex) return text;
  const [r, g, b] = hexToRgb(hex);
  return `\x1b[38;2;${r};${g};${b}m${text}\x1b[0m`;
}

const SPINNER_FRAMES = ["◐", "◓", "◑", "◒"];

/** Braille-style spinner frame for the given elapsed time, ~90ms per frame -- matches the cadence Pi's own setWorkingIndicator() uses. */
export function spinnerFrame(elapsedMs: number, intervalMs = 90): string {
  const index = Math.floor(elapsedMs / intervalMs) % SPINNER_FRAMES.length;
  return SPINNER_FRAMES[index];
}
