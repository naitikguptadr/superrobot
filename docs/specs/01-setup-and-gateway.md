# Spec 01 — Setup, Auth, and LLM Gateway

## Goal
First-run SuperRobot onboarding that is DataRobot-native: detect `dr`, authenticate via `dr auth` or API token, normalize Platform endpoints, verify LLM Gateway, and probe Agent App / Workload / Memory capabilities.

## Acceptance
- `superrobot doctor` exits 0 only when Gateway verifies and auth is present.
- Endpoint URLs ending in `/api/v2` or trailing slashes normalize correctly.
- Prediction API URLs are rejected.
- Capability probe records `agent_app`, `workload`, `memory` availability without writing secrets.
- Config persists under a configurable config dir (default `~/.config/superrobot`) with token file mode `0600`.
- No Anthropic/OpenAI direct keys required for the product path.

## Non-goals
- Full Pi TUI chrome (Spec 02).
- Transform / deploy (later specs).
