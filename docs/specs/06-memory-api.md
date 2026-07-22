# Spec 06 — Memory API Space Provisioning

## Goal
First-class, capability-gated access to the DataRobot Memory (Agentic Memory) API:
idempotently ensure a named memory space exists for a generated agent.

## Acceptance
- `superrobot memory ensure <name>` creates the space if absent, no-ops if present
  (mocked HTTP in unit tests).
- Capability probe: when Memory entitlement missing, CLI exits non-zero with a clear
  message — same pattern as the Spec 05 Workload gate.
- `--json` emits `{success, action, space_id, error_message}` without secrets.
- Unit tests for the client (find/create) and the provisioner (idempotent ensure).
- Skill `superrobot-memory` documents the command.

## Non-goals
- Reading/writing individual memory entries within a space.
- Automatic space provisioning during `transform`/`deploy` (explicit command only).
