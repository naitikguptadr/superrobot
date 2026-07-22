---
name: superrobot-agent-app-deploy
description: Deploy a SuperRobot-generated Agent App package via DataRobot `dr task run deploy`. Use after transform/generate when the target is Agent App.
---

# SuperRobot Agent App Deploy

```bash
superrobot deploy <generated-dir> --target agent-app
superrobot deploy <generated-dir> --target agent-app --json
```

Preconditions:
- `superrobot doctor` ready (`dr` on PATH for live deploy).
- Directory contains generated Agent App layout (`agent/agent/`, `infra/`, etc.).

Warnings (always shown):
- Deploy may take 15–20 minutes (BUZZOK-30076).
- Pulumi failure deletes deployment logs — prefer preserving logs via UI if debugging.
