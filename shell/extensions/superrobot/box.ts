import { visibleWidth } from "@mariozechner/pi-tui";

/**
 * Wrap plain or ANSI-colored rows in a box whose borders are computed from the
 * widest row's *visible* width -- never hand-padded, so embedded color codes
 * or unicode glyphs can't drift the edges out of alignment.
 */
export function boxLines(rows: string[], minWidth = 0): string[] {
  const width = Math.max(minWidth, 0, ...rows.map((row) => visibleWidth(row)));
  const pad = (row: string): string => row + " ".repeat(Math.max(0, width - visibleWidth(row)));
  const top = `┌${"─".repeat(width + 2)}┐`;
  const bottom = `└${"─".repeat(width + 2)}┘`;
  const body = rows.map((row) => `│ ${pad(row)} │`);
  return [top, ...body, bottom];
}
