---
name: superrobot-memory
description: Ensure a named DataRobot Memory (Agentic Memory) API space exists for a migrated agent. Use when a generated agent needs persistent memory and the account has the Memory capability.
---

# SuperRobot Memory

```bash
superrobot memory ensure <space-name>
superrobot memory ensure <space-name> --json
```

Preconditions:
- `superrobot doctor` ready and the account has the Memory capability (probed via
  `genai/agenticMemory/spaces/`) — checked before any API call.

Behavior:
- Idempotent get-or-create: looks up the space by name; existing space → no-op
  (`action: "found"`), missing → creates it (`action: "created"`).
- `--json` emits `{success, action, space_id, error_message}` without secrets.
- Does not read or write individual memory entries — space provisioning only.
