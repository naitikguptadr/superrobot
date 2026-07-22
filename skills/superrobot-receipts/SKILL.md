---
name: superrobot-receipts
description: Inspect, diagnose, and retry SuperRobot deploy attempts via their receipts. Use after a `superrobot deploy` (agent-app or workload) to see attribution/history, understand a failure, or retry after remediation.
---

# SuperRobot Receipts

Every `superrobot deploy` attempt — blocked by Gap Analysis, failed, or successful —
writes exactly one receipt under `~/.config/superrobot/receipts/<id>.json` (or under
`--config-dir` when set). No secrets are ever included: receipts carry attribution
(which Gateway model was configured), a Gap Analysis summary, and a short error
message, never credentials or secret values.

```bash
superrobot receipt show                 # latest
superrobot receipt show <id>
superrobot receipt operations [--target agent-app|workload]
superrobot receipt diagnose <id>
superrobot receipt replace <id> [--waive] [--secret KEY=credential:<id>]
```

- `show` — one receipt: target, action, success, model, Gap Analysis summary, error.
- `operations` — history table/JSON, newest first, optionally filtered by target.
- `diagnose` — pattern-matches `error_message` against known SuperRobot gotchas
  (single-replica replace block, plaintext-secret preflight, BUZZOK-30076 Pulumi log
  deletion, missing entitlement, Gap Analysis blocking) and prints a one-line fix.
- `replace` — re-runs the exact deploy a receipt captured (same target/dir, and
  `image_uri` for workload), recording a new receipt with `replaces: <id>`. Runs
  through the normal Gap Analysis gate again — pass `--waive` explicitly if the
  original was gap-blocked and you're intentionally proceeding anyway. Secret values
  are never persisted in a receipt, so workload replays need `--secret` again if the
  original deploy used one.
