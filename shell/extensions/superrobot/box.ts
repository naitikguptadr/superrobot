import { visibleWidth } from "@mariozechner/pi-tui";

/**
 * Wrap plain or ANSI-colored rows in a box whose borders are computed from the
 * widest row's *visible* width -- never hand-padded, so embedded color codes
 * or unicode glyphs can't drift the edges out of alignment.
 */
export function boxLines(inputRows: string[], minWidth = 0): string[] {
  // A row containing a newline would split across terminal lines with no
  // border, breaking the box -- callers may pass content sourced from CLI
  // stderr tails or free-text messages that aren't guaranteed single-line.
  const rows = inputRows.map((row) => row.replace(/\r?\n/g, " "));
  const width = Math.max(minWidth, 0, ...rows.map((row) => visibleWidth(row)));
  const pad = (row: string): string => row + " ".repeat(Math.max(0, width - visibleWidth(row)));
  const top = `┌${"─".repeat(width + 2)}┐`;
  const bottom = `└${"─".repeat(width + 2)}┘`;
  const body = rows.map((row) => `│ ${pad(row)} │`);
  return [top, ...body, bottom];
}
