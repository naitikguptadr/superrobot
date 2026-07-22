# Spec 03 — Transform Engine (Scan → Analyze → Generate → Eval)

## Goal
Port the battle-tested Python brownfield transform core into the rebuild tree and expose it as headless CLI + skill tooling. No Textual TUI. Gateway-backed analyze with deterministic fallback.

## Acceptance
- `superrobot scan <path> --json` emits valid `ScanResult`.
- `superrobot analyze <path> --json` emits valid `AnalysisResult` (fallback OK without live Gateway).
- `superrobot generate <path> --output-dir <dir>` writes DR-compliant Agent App files (flat imports, three-location runtime params, additive pyproject, `dr_llm` shim).
- `superrobot transform <path> --json --skip-eval` runs Scan→Analyze→Generate and prints a JSON summary.
- Fixture suite green for scanner, analyzer (mocked), config generator, ast migrate, schema inference, engine orchestrator.
- No secrets in generated files.
- Platform rules validators enforce DRUM flat imports and runtime param locations.

## Non-goals
- Agent App deploy CLI (Spec 04).
- Workload / Memory / Gap / receipts (Phase 2 specs).
- Premium Pi chrome beyond existing Spec 02 shell entrypoint.
- UI generator / Textual panels.

## Ported from archive
- `pipeline/{scanner,analyzer,config_generator,ast_migrate,schema_inference,evaluator}`
- `engine/{pipeline,context,providers}`
- `models/*`, `dr/{framework_mapper,platform_rules,llm_gateway,cli_wrapper,prompts}`
- Agent App Jinja templates + `tests/fixtures/*`
