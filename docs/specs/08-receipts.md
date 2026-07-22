# Spec 08 — Receipts + Attribution

## Goal
Every `superrobot deploy` attempt (agent-app or workload; blocked, failed, or
successful) writes exactly one non-secret receipt, and `receipt show|operations|
diagnose|replace` operate on the receipt history.

## Acceptance
- A receipt is written after every `deploy` invocation: `action="blocked"` when Gap
  Analysis blocks before any API call, otherwise the target's real outcome
  (`deployed`/`failed` for agent-app, `created`/`replaced`/`failed` for workload).
- Receipts never contain secret values — only attribution (`model`), a Gap Analysis
  summary (counts + waived finding messages), and a short `error_message`.
- `superrobot receipt show [<id>]` defaults to the latest receipt.
- `superrobot receipt operations [--target T]` lists receipts newest first.
- `superrobot receipt diagnose <id>` pattern-matches `error_message` against known
  SuperRobot failure modes (single-replica replace, plaintext secret, Pulumi log
  deletion, missing entitlement, Gap Analysis blocking) and returns a one-line fix.
- `superrobot receipt replace <id>` re-runs the deploy a receipt captured, through the
  normal Gap Analysis gate (not auto-waived), recording a new receipt with
  `replaces: <id>`.
- `--json` on all four subcommands, no secrets.
- Unit tests cover the store (save/load/list/latest), `diagnose`'s pattern matches
  and fallback, and CLI coverage for all four subcommands.

## Non-goals
- Long-term receipt retention/rotation policy (receipts accumulate under
  `~/.config/superrobot/receipts/` indefinitely for now).
- Cross-machine receipt sync.
