# Handoff — SuperRobot Pi Hybrid Rebuild

**Date:** 2026-07-22
**Status:** Specs 01–08 complete and committed. Shell rewired against Pi's real
extension/theme API. Verification matrix + golden-path demo written. Only the
cutover PR (archive tag + push + PR against `feat/brownfield-pipeline-and-tui`,
no merge) remains.

---

## Where to work

| Role | Path | Branch / ref |
|---|---|---|
| **Active rebuild** | `/Users/naitikgupta/Projects/superrobot` | `rebuild/pi-datarobot` |
| Old product (pre-rebuild) | same repo | `feat/brownfield-pipeline-and-tui` (GitHub default branch) |
| Rollback tag | same repo | `archive/pre-pi-rebuild` — created during cutover (Phase F), not before |

Single clone, two branches — no separate archive checkout needed; use
`git show <branch>:<path>` or `git ls-tree -r <branch>` to read the old tree without
switching branches. Branch is pushed to `origin/rebuild/pi-datarobot`.

See "Path note" below for why this differs from earlier handoff versions.

---

## Strategy (locked)

- Orphan branch → replace the default branch later via a reviewable PR, not a force-push.
- Spec-by-spec; one commit per vertical slice; acceptance green before next spec.
- **Pi-customized shell** = UX; **Python engine** = scan/migrate/eval/deploy/receipts.
- Do **not** rewrite migration engine in TypeScript.
- Do **not** bulk-copy Textual TUI.
- DataRobot spine only: LLM Gateway, `dr` and/or API token, Agent App, Workload, Memory (when entitled).

---

## Done

### Specs 01–04 — Setup/Gateway, Premium shell bootstrap, Transform engine, Agent App deploy
See `docs/specs/01-setup-and-gateway.md` through `04-agent-app-deploy.md` and
`docs/verification-matrix.md` for what's implemented and how it's tested. No
`superrobot/deployment/` or `superrobot/workload/` ever existed anywhere in this
repo's history (confirmed on 2026-07-22) — Specs 05+ were built fresh against their
spec docs and the established `dr/` client + `pipeline/` orchestrator + gated CLI
subcommand + skill doc + tests pattern, not ported from an archive.

### Spec 05 — Workload API deploy
`dr/workload_client.py` (find/create/replace) + `pipeline/workload_deployer.py`
(preflight: blocks replace below 2 replicas, blocks non-`credential:` secret values).
`deploy --target workload --image-uri <uri> [--secret KEY=credential:<id>]`, gated on
`SetupState.capabilities.workload`.

### Spec 06 — Memory API
`dr/memory_client.py` + `pipeline/memory_provisioner.py` (idempotent get-or-create).
`memory ensure <name>`, gated on `SetupState.capabilities.memory`.

### Spec 07 — Gap Analysis + validate gates
`pipeline/gap_analysis.py` re-runs `dr/platform_rules.py`'s validators (previously
only fired as unenforced Python `warnings`) against a generated package, classified
blocking/warning. `superrobot validate <dir>`; `deploy` refuses on blocking findings
unless `--waive`. Matches `shell/prompts/system.md`'s pre-existing rule: *"Gap
Analysis findings that are blocking must stop deploy. Warnings need explicit waiver."*

### Spec 08 — Receipts + attribution
`models/receipt.py` + `pipeline/receipts.py`. Every `deploy` attempt (blocked,
failed, or successful) writes one non-secret receipt under
`~/.config/superrobot/receipts/`. `receipt show|operations|diagnose|replace`.
`replace` re-runs through the *normal* Gap Analysis gate (not auto-waived).

**Bug found and fixed while building the Spec E demo:** `dr/cli_wrapper.py`'s
subprocess calls raised an unhandled `FileNotFoundError` when `dr` wasn't on PATH,
crashing before a receipt could be written — violating Spec 08's "every deploy
attempt writes a receipt" guarantee. Now returns a clean `DrCommandResult` instead.

### Shell deepening (Spec 02 follow-on)
`shell/node_modules` was never installed in this rebuild before now. Read
`@mariozechner/pi-coding-agent`'s own bundled docs before touching anything — the
previous `theme.json` (`bg`/`typography`/`motion` fields) and env-var wiring
(`PI_THEME`, `OPENAI_BASE_URL`/`OPENAI_API_KEY`/`OPENAI_MODEL`,
`SUPERROBOT_SYSTEM_PROMPT`) matched **none** of Pi's real config surface — Pi's theme
schema has no background/typography/motion tokens at all, and none of those env vars
are read anywhere in Pi. Rewired for real:
- `shell/theme/superrobot.theme.json` — real 51-token schema.
- `shell/extensions/superrobot.ts` (new) — registers a DataRobot Gateway provider via
  `pi.registerProvider()`, selects it via `pi.setModel()`, registers the theme dir via
  `resources_discover`, renders capability chips via `ctx.ui.setStatus()` (reads
  `~/.config/superrobot/setup.json` directly, no Python subprocess dependency).
- `shell/src/cli.ts` — spawns `pi` with real `-e`/`--extension` and `--system-prompt`
  flags instead of ignored env vars.
- Verified: build/typecheck clean (including the new `shell/extensions/tsconfig.json`),
  plus a live non-interactive `pi --print` run that loaded the extension, registered/
  selected the Gateway model, and reached the real DataRobot Gateway endpoint (401 for
  a placeholder token — proves the request path end-to-end).
- **Known limitation, documented not hidden:** chip/theme *rendering* in the
  interactive TUI needs a human at a live terminal — no tool here attaches to an
  interactive TTY. See `docs/ui-qa.md`, corrected to stop claiming background/
  typography/motion effects Pi's theme system was never capable of.

### Docs
`docs/verification-matrix.md` (spec → acceptance → actual test file), `docs/demo.md`
+ `scripts/demo.sh` (runnable golden path, verified to run clean end-to-end). Neither
fabricates a Swarm integration — see `docs/verification-matrix.md`'s "Handoff
boundary" section for why (no Swarm client/schema exists anywhere in this repo or its
history; only Gap Analysis's and receipts' `--json` output are real, tested handoff
surfaces today).

### Verification last green
```bash
cd /Users/naitikgupta/Projects/superrobot
uv sync --all-extras
uv run ruff check . && uv run ruff format --check . && uv run mypy superrobot
uv run pytest tests/unit -q   # 131 passed
cd shell && npm run typecheck && npm run build
./scripts/demo.sh   # golden path, no real credentials needed
```

### Path note (2026-07-22, kept for history)
An earlier version of this file referenced `/Users/naitik.gupta/workspace/superrobot-v2`
and `~/.cursor/plans/pi-hybrid-rebuild_3840b8d4.plan.md` — neither exists on this
machine (home is `naitikgupta`, no dot; no such plan file). The actual clone is
`/Users/naitikgupta/Projects/superrobot`, which had `origin/rebuild/pi-datarobot` as a
remote branch — checked out locally to continue. This file plus `docs/specs/*.md` are
the working source of truth here.

---

## In progress / dirty tree

None — working tree matches HEAD after every commit in this pass.

---

## Remaining plan order

1–5. ~~Specs 05–08 + shell deepening~~ done
6. ~~Verification matrix + demo~~ done
7. **Cutover PR** — tag `archive/pre-pi-rebuild` on `feat/brownfield-pipeline-and-tui`,
   push `rebuild/pi-datarobot`, open a PR proposing the full-tree replace. **Do not
   merge it or change the default branch** — that's a human call, reviewed via the PR.

---

## Product constraints (do not reopen)

- Standalone CLI remains; also deploy + re-attribution for Swarm / Gap.
- Gap Analysis = **Agent Assist skill**, not a new agent.
- Swarm emits shared `SimulationEvidence` (Gateway model + tokens) — **not
  implemented**; no Swarm client/schema exists anywhere in this repo. Building one
  needs a real API contract from whoever owns Swarm, not a guess.
- UI bar: deep Pi customization, not a logo swap.
- No secrets in receipts/git/UI logs.

---

## Suggested first message for next agent

```text
Continue the SuperRobot Pi hybrid rebuild in /Users/naitikgupta/Projects/superrobot
on branch rebuild/pi-datarobot. Read HANDOFF.md (no external plan file exists on this
machine — HANDOFF.md + docs/specs/*.md + docs/verification-matrix.md are the source
of truth).

Specs 01–08 are done, shell is rewired against Pi's real API, docs/demo.md +
scripts/demo.sh are verified working. The only remaining item is the cutover PR
(archive tag + push + PR against feat/brownfield-pipeline-and-tui, no merge) — check
whether it's already been opened (`gh pr list`) before redoing it.

If asked to build the Swarm integration: there is nothing to port or extend — it does
not exist in this codebase. Get a real API contract before writing a client.

Do not touch the Textual TUI. Do not rewrite the engine in TypeScript.
Spec-by-spec commits only.
```
