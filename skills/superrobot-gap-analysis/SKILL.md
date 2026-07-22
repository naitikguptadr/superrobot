---
name: superrobot-gap-analysis
description: Run Gap Analysis (platform-rule checks) against a SuperRobot-generated package before deploy, or diagnose a deploy that was refused. This is an Agent Assist skill — it augments the existing SuperRobot CLI/agent, it is not a separate agent.
---

# SuperRobot Gap Analysis

```bash
superrobot validate <generated-dir>
superrobot validate <generated-dir> --source <original-repo>   # also checks pyproject removal
superrobot validate <generated-dir> --json
```

Checks (reusing `superrobot/dr/platform_rules.py`, the same validators
`config_generator.py` runs at generation time):
- **blocking** — nested (non-flat) DRUM imports; dependencies removed vs. the
  original `pyproject.toml` (only with `--source`); directory doesn't look like a
  generated SuperRobot package at all.
- **warning** — a scanned env var missing from `_RUNTIME_PARAM_KEYS` or
  `infra/infra/agent.py`; `DATAROBOT_PREDICTION_API_URL` used alongside
  `dr.Client()` (likely meant `DATAROBOT_ENDPOINT`).

Deploy contract (`superrobot deploy`):
- Blocking findings **stop deploy** — exit 1, no `dr`/Workload API call is made.
- Pass `--waive` to proceed anyway; the waiver is recorded (Spec 08 receipts).
- Warnings are printed but never block — no waiver needed.

This mirrors the rule already encoded in `shell/prompts/system.md`: *"Gap Analysis
findings that are blocking must stop deploy. Warnings need explicit waiver."*
