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

### Verification last green
```bash
cd /Users/naitik.gupta/workspace/superrobot-v2
uv sync --all-extras
uv run ruff check . && uv run ruff format --check . && uv run mypy superrobot
uv run pytest tests/unit -q   # 73 passed
cd shell && npm run build && npm run typecheck
```

Smoke:
```bash
uv run superrobot scan tests/fixtures/langchain_agent --json
uv run superrobot transform tests/fixtures/langchain_agent --json --skip-eval -o /tmp/sr-out
```

---

## In progress / dirty tree

- **Untracked:** `docs/specs/05-workload-deploy.md` (Spec 05 checklist written; **no workload code committed yet**).
- Auto-review **blocked** a bulk `cp` of `workload/` + `deployment/` from the archive into v2. Next agent should either:
  1. Get user approval for intentional archive port, or
  2. Re-implement/port file-by-file via Read→Write (as done for workload Jinja templates).

Archive sources to port for Spec 05:
- `superrobot/deployment/*.py`
- `superrobot/workload/*.py`
- Tests: `tests/unit/test_workload_*.py`, `test_deployment_contracts.py`
- Then wire CLI `deploy --target workload --image-uri …` + skill `superrobot-workload-deploy`

---

## Remaining plan order

1. **Spec 05** — Workload deploy / preflight / 2-replica replace guard  
2. **Spec 06** — Memory API behind capability flag  
3. **Spec 07** — Gap Analysis skill + `validate` gates  
4. **Spec 08** — Receipts + attribution + `receipt show|operations|diagnose|replace`  
5. Deepen Spec 02 shell (graph canvas, status theater, visual QA) if demo needs it  
6. Swarm/Gap handoff + demo + verification matrix  
7. Cutover PR: full-tree replace of `main`; keep `archive/pre-pi-rebuild`

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
Continue the SuperRobot Pi hybrid rebuild in /Users/naitik.gupta/workspace/superrobot-v2
on branch rebuild/pi-datarobot. Read HANDOFF.md and ~/.cursor/plans/pi-hybrid-rebuild_3840b8d4.plan.md.

Specs 01–04 are committed (3df5d81). Next: Spec 05 Workload — intentionally port
deployment/ + workload/ + tests from archive /Users/naitik.gupta/workspace/superrobot
(tag archive/pre-pi-rebuild / branch feat/brownfield-pipeline-and-tui), wire
`superrobot deploy --target workload`, commit when unit tests green.

Do not touch the Textual TUI. Do not rewrite the engine in TypeScript.
Spec-by-spec commits only. Archive is reference/port source, not the product tree.
```
