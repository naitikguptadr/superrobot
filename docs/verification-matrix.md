# Verification Matrix

One row per spec. "Verified by" names the actual test file(s)/CLI command that check
the acceptance criteria in `docs/specs/NN-*.md` — this doc is generated from what
exists today, not an aspirational roadmap. Re-run everything:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy superrobot
uv run pytest tests/unit -q   # 129 passed as of this doc
cd shell && npm run typecheck && npm run build
```

| Spec | Acceptance (summary) | Verified by |
|---|---|---|
| [01 — Setup & Gateway](specs/01-setup-and-gateway.md) | `doctor` readiness gate; endpoint normalize; Prediction-API rejection; capability probe; `0600` token file | `tests/unit/setup/test_spec01.py` |
| [02 — Premium Shell](specs/02-premium-shell.md) | `superrobot` binary; Gateway-only provider; theme/layout under `shell/`; print/json mode; visual QA checklist | `shell/` `npm run build`/`typecheck` (incl. `extensions/tsconfig.json`); `docs/ui-qa.md` (checklist + honestly-scoped limitations) |
| [03 — Transform Engine](specs/03-transform-engine.md) | `scan`/`analyze`/`generate`/`transform`; deterministic fallback; DRUM-flat migration | `tests/unit/pipeline/{test_scanner,test_analyzer,test_config_generator,test_ast_migrate,test_schema_inference,test_complex_import}.py`, `tests/unit/engine/test_pipeline.py`, `tests/unit/dr/{test_framework_mapper,test_llm_gateway,test_platform_rules}.py` |
| [04 — Agent App Deploy](specs/04-agent-app-deploy.md) | `deploy --target agent-app`; BUZZOK-30076 warnings; `--json`; success/failure/UI-warning-filter tests | `tests/unit/pipeline/test_deployer.py`, `tests/unit/test_cli.py` |
| [05 — Workload Deploy](specs/05-workload-deploy.md) | `deploy --target workload --image-uri`; single-replica-replace + plaintext-secret preflight; capability gate | `tests/unit/dr/test_workload_client.py`, `tests/unit/pipeline/test_workload_deployer.py`, `tests/unit/test_cli.py` |
| [06 — Memory API](specs/06-memory-api.md) | `memory ensure <name>`; idempotent get-or-create; capability gate | `tests/unit/dr/test_memory_client.py`, `tests/unit/pipeline/test_memory_provisioner.py`, `tests/unit/test_cli.py` |
| [07 — Gap Analysis](specs/07-gap-analysis.md) | `validate <dir>`; blocking stops deploy, `--waive` overrides, warnings never block | `tests/unit/pipeline/test_gap_analysis.py`, `tests/unit/test_cli.py` |
| [08 — Receipts](specs/08-receipts.md) | Every deploy attempt writes one non-secret receipt; `receipt show/operations/diagnose/replace` | `tests/unit/pipeline/test_receipts.py`, `tests/unit/test_cli.py` |

## Handoff boundary (Swarm / Gap Analysis)

`HANDOFF.md`'s locked constraints mention Swarm consuming Gap Analysis findings and
emitting shared `SimulationEvidence`. There is no Swarm client, endpoint, or schema
anywhere in this codebase or its history — "SimulationEvidence" appears only as a
one-line constraint with no further definition. The actual, verified handoff surface
this repo offers today is:

- `superrobot validate <dir> --json` — structured `GapReport` (blocking/warning
  findings), no secrets.
- `superrobot receipt operations --json` / `receipt show <id> --json` — structured
  `Receipt` history (attribution, Gap Analysis summary, outcome), no secrets.

Both are real, tested, JSON-stable outputs an external system could consume today.
Wiring an actual Swarm integration needs a real API contract supplied by whoever owns
Swarm — it was not fabricated here.
