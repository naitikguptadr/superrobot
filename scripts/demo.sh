#!/usr/bin/env bash
# Golden-path demo: scan -> analyze -> transform -> validate -> deploy -> receipts.
#
# Runs entirely against tests/fixtures/langchain_agent with an isolated
# --config-dir, so it needs no real DataRobot credentials and never touches
# ~/.config/superrobot/. The agent-app deploy step is expected to fail cleanly
# with "dr: command not found" unless the `dr` CLI is on PATH — that failure
# (with a written receipt) is itself part of what this demo shows.
set -euo pipefail
cd "$(dirname "$0")/.."

FIXTURE="tests/fixtures/langchain_agent"
OUT="$(mktemp -d)"
CONFIG="$(mktemp -d)"
trap 'rm -rf "$OUT" "$CONFIG"' EXIT

hr() { printf '\n=== %s ===\n' "$1"; }

hr "1/6 scan"
uv run superrobot scan "$FIXTURE" --json

hr "2/6 analyze"
uv run superrobot analyze "$FIXTURE" --json

hr "3/6 transform (scan + analyze + generate, skip 5-shot eval for speed)"
uv run superrobot transform "$FIXTURE" --json --skip-eval -o "$OUT"

hr "4/6 validate (Gap Analysis against the generated package)"
uv run superrobot validate "$OUT" --source "$FIXTURE" --json

hr "5/6 deploy --target agent-app (--waive: fixture has no real dr/Pulumi state)"
uv run superrobot deploy "$OUT" --target agent-app --config-dir "$CONFIG" --waive --json || true

hr "6/6 receipt operations (the deploy attempt above, success or not, is here)"
uv run superrobot receipt operations --config-dir "$CONFIG" --json
