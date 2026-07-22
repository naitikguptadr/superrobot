# Spec 04 — Agent App Deploy

## Goal
Expose Agent App deployment via `dr task run deploy` as a first-class CLI/skill on top of generated packaging. Surface known deploy gotchas before the call. Keep Workload API for a later Phase 2 spec.

## Acceptance
- `superrobot deploy <dir> --target agent-app` invokes `dr task run deploy` (mocked in unit tests).
- Prints BUZZOK-30076 / logs-deleted warnings before deploy.
- `--json` emits `{success, warnings, error_message}` without secrets.
- Skill `superrobot-agent-app-deploy` documents the command.
- Unit tests cover success, failure parse, and UI-aware warning filtering.
- Requires `dr` on PATH for live deploy; unit path uses `DrCliWrapper` injection.

## Non-goals
- Workload API deploy/operate/replace.
- Gap Analysis gates.
- Receipts / re-attribution.
