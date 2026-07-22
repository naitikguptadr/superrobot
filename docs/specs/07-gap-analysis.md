# Spec 07 — Gap Analysis + Validate Gates

## Goal
Surface the platform-rule checks that already exist in `dr/platform_rules.py` (and were
previously only fired as Python `warnings` during generation) as a first-class, CLI-
exposed Gap Analysis report, and make `deploy` enforce the blocking/warning contract
already documented in the shell's system prompt.

## Acceptance
- `superrobot validate <dir>` runs Gap Analysis against a generated package and prints
  findings; exit 1 iff any finding is `blocking`.
- `--source <repo>` additionally enables the pyproject-dependency-removal check against
  the original repo.
- `superrobot deploy` runs Gap Analysis before every deploy attempt (both targets):
  blocking findings abort with exit 1 and no `dr`/Workload API call is made, unless
  `--waive` is passed.
- Warnings are always printed but never block, with or without `--waive`.
- `--json` on both commands emits structured output with no secrets.
- Skill `superrobot-gap-analysis` documents this as an Agent Assist skill, not a new
  agent (per the locked product constraints in `HANDOFF.md`).
- Unit tests cover: flat-import violations, endpoint-usage warnings, runtime-param
  cross-check warnings, pyproject-removal blocking (with `--source`), a directory that
  isn't a generated package, and deploy being blocked/waived.

## Non-goals
- Inventing new platform gotchas beyond what `platform_rules.py` already encodes.
- Receipts / attribution (Spec 08 — waiver logging lands there).
