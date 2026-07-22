---
name: superrobot-workload-deploy
description: Deploy a SuperRobot-generated Workload package via the DataRobot Workload API. Use after transform/generate when the target is Workload (containerized service, not Agent App).
---

# SuperRobot Workload Deploy

```bash
superrobot deploy <generated-dir> --target workload --image-uri <uri>
superrobot deploy <generated-dir> --target workload --image-uri <uri> --secret API_KEY=credential:<id>
superrobot deploy <generated-dir> --target workload --image-uri <uri> --json
```

Preconditions:
- `superrobot doctor` ready and the account has the Workload capability
  (`ENABLE_WORKLOAD_API_CONTAINERS`) — checked before any API call.
- Directory contains generated Workload packaging (`workload/workload.yaml`,
  `workload/Dockerfile`, `agent/agent/workload_service.py`) — build and push
  `<uri>` first; SuperRobot does not build or push images.

Behavior:
- Looks up an existing workload by name (from `workload.yaml`). No match →
  create. Match → rolling replace.
- Preflight blocks a replace when any container group's `replicaCount` is
  below 2 — scale the live workload up first.
- Preflight blocks `--secret` values that aren't a credential reference
  (`credential:<id>`); plaintext secret values are rejected before any
  network call.
- `--json` emits `{success, action, workload_id, error_message}` without
  secrets.
