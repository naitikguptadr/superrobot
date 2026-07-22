# Handoff — SuperRobot Pi Hybrid Rebuild

**Date:** 2026-07-22  
**Status:** Specs 01–04 complete and committed; Spec 05 started (doc only, uncommitted).  
**Authoritative plan:** `~/.cursor/plans/pi-hybrid-rebuild_3840b8d4.plan.md`

---

## Where to work

| Role | Path | Branch / ref |
|---|---|---|
| **Active rebuild (continue here)** | `/Users/naitik.gupta/workspace/superrobot-v2` | `rebuild/pi-datarobot` @ `3df5d81` |
| Archive / port source only | `/Users/naitik.gupta/workspace/superrobot` | `feat/brownfield-pipeline-and-tui` @ `6800fd1` |
| Rollback tag | same repo | `archive/pre-pi-rebuild` |

Cursor agent root was moved to `superrobot-v2`. Do **not** bulk-edit the archive tree except to **intentionally port** approved modules.

Branch is **local-only** (not pushed). Push before relying on remote fetch / some `move_agent_to_root` flows.

---

## Strategy (locked)

- Orphan branch + sibling worktree → replace `main` later via full-tree PR.
- Spec-by-spec; one commit per vertical slice; acceptance green before next spec.
- **Pi-customized shell** = UX; **Python engine** = scan/migrate/eval/deploy/receipts.
- Do **not** rewrite migration engine in TypeScript.
- Do **not** bulk-copy Textual TUI.
- DataRobot spine only: LLM Gateway, `dr` and/or API token, Agent App, Workload, Memory (when entitled).
- Port from archive only when a spec needs it (`pipeline/`, `engine/`, `workload/`, `deployment/`, templates, fixtures, `platform_rules`).

---

## Done

### Spec 01 — Setup / Gateway (`469f421` seed + later doctor isolation)
- `superrobot setup|doctor|status`
- Endpoint normalize (reject Prediction API), Gateway verify, capability probe
- Config under `~/.config/superrobot/` (token file mode `0600`)
- `--config-dir` isolation: when set, process env auth is ignored (tests)

### Spec 02 — Premium shell (bootstrap in seed commit)
- `shell/` Node package wrapping Pi (`@mariozechner/pi-coding-agent`, deprecation notice → earendil-works)
- Theme + system prompt + Gateway env wiring
- **Still thin** vs plan “million-dollar” UI bar — deepen later, do not block Phase 2 ports

### Spec 03 — Transform engine (`3ceae39`)
- Ported: `pipeline/*` (scan/analyze/generate/eval/deployer), `engine/*`, `models/*`, `dr/*`, templates, fixtures
- CLI: `scan`, `analyze`, `generate`, `transform --json`
- Skill: `skills/superrobot-transform/`
- Gateway analyze + deterministic fallback; credentials hydrate from setup `.env`

### Spec 04 — Agent App deploy (`3df5d81`)
- CLI: `deploy <dir> --target agent-app [--json] [--has-ui]`
- Skill: `skills/superrobot-agent-app-deploy/`
- Pre-deploy warnings (BUZZOK-30076 / logs deleted)

### Spec 05 — Workload deploy
- No `superrobot/deployment/`, `superrobot/workload/`, or `archive/pre-pi-rebuild` tag
  existed anywhere in the archive repo on this machine — built fresh from
  `docs/specs/05-workload-deploy.md` acceptance criteria and the Spec 04 pattern,
  not ported. `workload_yaml.j2` / `workload_Dockerfile.j2` / `workload_service_py.j2`
  templates already existed from Spec 03 and were reused as-is.
- `superrobot/dr/workload_client.py` — async Workload API client (find/create/replace),
  same injectable-transport shape as `setup/gateway.py`.
- `superrobot/pipeline/workload_deployer.py` — loads `workload/workload.yaml`, injects
  `--image-uri`, preflights (blocks replace below 2 replicas, blocks non-`credential:`
  secret values) before any network call, then create-or-replace via `WorkloadClient`.
- CLI: `deploy <dir> --target workload --image-uri <uri> [--secret KEY=credential:<id>] [--json]`.
  Gated on persisted `SetupState.capabilities.workload` (from `doctor`/`setup`) — exits 1
  with a clear message if the account isn't entitled.
- Skill: `skills/superrobot-workload-deploy/`
- Tests: `tests/unit/dr/test_workload_client.py`, `tests/unit/pipeline/test_workload_deployer.py`,
  3 new CLI cases in `tests/unit/test_cli.py`. 92/92 unit tests green, ruff + mypy clean.

### Verification last green
```bash
cd /Users/naitikgupta/Projects/superrobot   # NOT superrobot-v2 — see path note below
uv sync --all-extras
uv run ruff check . && uv run ruff format --check . && uv run mypy superrobot
uv run pytest tests/unit -q   # 92 passed
cd shell && npm run build && npm run typecheck   # unchanged by Spec 05, not re-run
```

### Path note (2026-07-22)
`/Users/naitik.gupta/workspace/superrobot-v2` does not exist on this machine (home is
`naitikgupta`, no dot). The actual clone is `/Users/naitikgupta/Projects/superrobot`,
which had `origin/rebuild/pi-datarobot` as a remote branch — checked it out locally to
continue. `~/.cursor/plans/pi-hybrid-rebuild_3840b8d4.plan.md` is also not on this
machine; this file plus `docs/specs/*.md` are the working source of truth here instead.

Smoke:
```bash
uv run superrobot scan tests/fixtures/langchain_agent --json
uv run superrobot transform tests/fixtures/langchain_agent --json --skip-eval -o /tmp/sr-out
```

---

## In progress / dirty tree

None — Spec 05 committed clean, working tree matches HEAD.

---

## Remaining plan order

1. ~~**Spec 05** — Workload deploy / preflight / 2-replica replace guard~~ done
2. **Spec 06** — Memory API behind capability flag  
3. **Spec 07** — Gap Analysis skill + `validate` gates  
4. **Spec 08** — Receipts + attribution + `receipt show|operations|diagnose|replace`  
5. Deepen Spec 02 shell (graph canvas, status theater, visual QA) if demo needs it  
6. Swarm/Gap handoff + demo + verification matrix  
7. Cutover PR: full-tree replace of `main`; keep an archive reference point (no
   `archive/pre-pi-rebuild` tag exists in this repo — create one before cutover, or
   confirm the equivalent already exists wherever `main` actually lives)

---

## Product constraints (do not reopen)

- Standalone CLI remains; also deploy + re-attribution for Swarm / Gap.
- Gap Analysis = **Agent Assist skill**, not a new agent.
- Swarm emits shared `SimulationEvidence` (Gateway model + tokens).
- UI bar: deep Pi customization, not a logo swap.
- No secrets in receipts/git/UI logs.

---

## Suggested first message for next agent

```text
Continue the SuperRobot Pi hybrid rebuild in /Users/naitikgupta/Projects/superrobot
on branch rebuild/pi-datarobot. Read HANDOFF.md (no external plan file exists on this
machine — HANDOFF.md + docs/specs/*.md are the source of truth).

Specs 01–05 are committed. Next: Spec 06 Memory API behind a capability flag —
follow the Spec 05 pattern (dr/ client with injectable transport, pipeline/ orchestrator,
CLI subcommand gated on SetupState.capabilities, skill doc, unit tests). There is no
Memory-related code in this repo yet to port from anywhere; build it fresh against
docs/specs (write docs/specs/06-memory-api.md first if it doesn't exist) and the
platform's Memory API shape referenced in CLAUDE.md.

Do not touch the Textual TUI. Do not rewrite the engine in TypeScript.
Spec-by-spec commits only.
```
