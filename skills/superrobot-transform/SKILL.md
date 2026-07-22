---
name: superrobot-transform
description: Scan, analyze, and generate DataRobot Agent App packaging for an existing Python agent repo. Use when migrating brownfield agents or when the user asks to import/transform/generate DR config.
---

# SuperRobot Transform

Run the Python engine (never invent DR packaging by hand):

```bash
superrobot scan <path> --json
superrobot analyze <path> --json
superrobot generate <path> --output-dir <dir>
superrobot transform <path> --json --skip-eval
```

Rules:
- Prefer Gateway-backed analyze; deterministic fallback is OK offline.
- Generated imports must be flat (DRUM).
- Runtime params in infra, custom.py, and .env.template.
- Never write secrets into generated files or JSON output.
