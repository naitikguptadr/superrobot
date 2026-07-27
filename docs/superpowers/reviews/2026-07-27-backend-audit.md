# Backend Code Review — Findings Log (2026-07-27)

**Status: IDENTIFY-ONLY. Nothing here has been fixed.** Triage record.

**Scope:** the Python backend, `superrobot/` (6,059 LOC). `superrobot/pipeline/graph/` was reviewed separately earlier and is excluded except where it's the reference for a bug class found elsewhere.

**Passes run:** a solo sweep, then two waves of three parallel subagent reviewers (six specialist passes total, split by subsystem). ~100 findings.

**Trust markers:** **[VERIFIED]** = reproduced, output shown in the pass. **[INSPECTION]** = read from code, consequence reasoned not observed. No inference is presented as fact.

---

## ⚠️ Headline: the tool is currently broken for its core use case

Four independently-verified defects, any one of which breaks a normal migration:

1. **Any repo with a `pyproject.toml` crashes the migration** (C1) — the whole tool, on the most common repo shape.
2. **Second and subsequent deploys silently ship the old image** (C2) — the user is told it worked.
3. **`generate`/`transform` silently destroy the user's files** (C3) — unrecoverable data loss, exit 0.
4. **The deploy gate is strictly weaker than `validate`** (C4) — a package `validate` blocks, `deploy` ships clean.

These are not edge cases. C1 and C3 fire on the default happy path.

---

## CRITICAL

### C1. `_merge_dependencies` treats every line of `pyproject.toml` as a package name — migration aborts [VERIFIED]
`pipeline/config_generator.py:294-304`

Walks *every line* of the user's pyproject, not the `dependencies` array. A multi-line array (i.e. essentially every real file) contributes a bare `]`, which sorts first into the generated array. `platform_rules._extract_dependencies`' non-greedy regex then terminates on that `]`, sees zero deps, and `validate_pyproject` reports every original package as removed.

```
_merge_dependencies -> [']', 'datarobot', 'dependencies = [', 'description = "A support agent',
                        'line-length = 100', 'name = "ticket-triage', 'requires-python = "', ...]
E2E: PyprojectRemovalError: Generated pyproject.toml removed packages: ['httpx','langchain-openai','langgraph']
```
`engine/pipeline.py:114` has no handler → **the entire migration dies.**

### C2. `replace()` calls a metadata-only endpoint — redeploys silently don't ship [VERIFIED against vendored spec]
`dr/workload_client.py:57-64`

Sends `PATCH /workloads/{id}/`. The repo's own vendored authority (`vendor/datarobot-agent-skills/skills/datarobot-workload-api/SKILL.md:119-123`) states that endpoint is **"Metadata only — no restart"**; swapping an artifact requires `POST /workloads/{id}/replacement/`.

Result: every deploy after the first reports `success=True, action="replaced"` while the live workload keeps serving the **old image**. Same bug class as the previously-shipped `/v1` gateway path.

### C3. `generate`/`transform` silently overwrite the output directory — including the source repo [VERIFIED]
`pipeline/config_generator.py:277-284` via `engine/pipeline.py:114`

No existence check, no `--force`, no prompt, no `output_dir != source` guard.
```
$ superrobot generate examples/research-agent -o /tmp/victim
wrote 7 files → /tmp/victim        EXIT=0
# /tmp/victim/AGENTS.md   "MY IMPORTANT NOTES" -> "# my-agent"
# /tmp/victim/pyproject.toml  name="user-project" v9.9.9 -> name="my-agent" v0.1.0
```
In-place (`-o .`, one keystroke) destroys the source repo's own `pyproject.toml`. The tool has a *blocking rule* to protect that file; `generate` deletes it.

### C4. The deploy gate runs a strictly weaker check set than `validate` [VERIFIED]
`cli.py:515` vs `cli.py:340`; `pipeline/gap_analysis.py:82,118`

`_gap_gate` calls `run_gap_analysis(path)` with no `source_repo`, which structurally disables the `pyproject-removal` **blocking** rule and all graph findings. `deploy` has no `--source` option, so this is unreachable, not merely default-off.
```
validate --source → blocking pyproject-removal, EXIT=1
validate          → no gaps found,               EXIT=0
deploy            → deployed, receipt says {"blocking":0,"waived_findings":[]}
```
The audit trail permanently asserts a gate passed that never ran.

### C5. Receipt write failure after a *successful* deploy → traceback + exit 1 [VERIFIED]
`cli.py:595-606`, `:682-694`
```
!! REAL DEPLOY HAPPENED (succeeded)
deploy succeeded
EXIT 1   NotADirectoryError: '/tmp/notadir/receipts'
```
Platform mutated, stdout says success, exit non-zero, no receipt. CI retries a deploy that already landed.

### C6. The unit test suite loads and spends the developer's real API token [VERIFIED]
`dr/llm_gateway.py:32` → `analyzer.py:19`

`ensure_credentials_loaded()` calls `load_env_file()` with **no `root`**, resolving to `~/.config/superrobot/.env`, then `os.environ.setdefault`s the developer's real credentials into the **process-global** environment. No `conftest.py` exists anywhere under `tests/`.
```
[ENVDIFF] after test_transform_engine_headless_generate:
  {} -> {'DATAROBOT_ENDPOINT': ..., 'DATAROBOT_API_TOKEN': '<real key>', ...}
```
`pytest` then makes live billed calls to staging. Any CI log or crash dump capturing `os.environ` holds a live token.

**This is also the root cause of the known flaky test** (see F1).

### C7. Non-200 on resource lookup is read as "does not exist" → duplicate create, safety guard bypassed [VERIFIED]
`dr/workload_client.py:44-49`, `dr/memory_client.py:44-49`

`if status != 200: return None` collapses 401/403/429/500/502 into "not found", so `deploy_workload` takes the **create** branch — never invoking `preflight_replace` (`workload_deployer.py:79-85`), the guard that refuses to roll below 2 replicas. Reproduced: a 500 on GET + 201 on POST → `action='created'`. In production the user sees `409 name conflict` with no hint the real failure was a transient 500 or an expired token.

### C8. `workflow.yaml` overrides a confirmed framework at 100% confidence [VERIFIED]
`pipeline/scanner.py:169-170`

`if (root/"workflow.yaml").exists(): detected_framework = "nat"` — no content check, no guard. A stray CI file beats every other signal. A CrewAI repo → `nat`, confidence **1.00**, and `nat` isn't in analyzer's `_needs_confirm`, so nothing asks. The bundle is generated against the wrong base class and cannot run.

### C9. `import openai` yields zero providers → deployed agent has no credentials [VERIFIED]
`pipeline/scanner.py:158-159`, `engine/providers.py:70`

Provider detection only inspects `ast.ImportFrom`. Plain `import openai` (the most common form) → `detected_providers=[]`, `env_vars=[]` → no runtime params in the generated bundle → **deployed agent fails auth on first LLM call.** Mirror bug: `from openai_helpers import x` (bare `startswith`, no boundary check) injects three bogus providers.

### C10. `os.environ["KEY"]` is not detected — the *required* env idiom is invisible [VERIFIED]
`pipeline/scanner.py:88`

`GETENV_PATTERN` matches only `os.getenv(...)` / `os.environ.get(...)`. So the scanner detects exactly the *optional* vars and misses the *mandatory* ones. Also fires inside comments and string literals. Verified E2E: repo with `os.environ["MY_REQUIRED_KEY"]` → `runtime_param_keys=[]` → deployed agent dies with `KeyError` at import.

### C11. Class methods and closures become entry points → unresolvable import in the bundle [VERIFIED]
`pipeline/scanner.py:283-293`

`ast.walk` with no module-level check. `class Bot: def run(self, q)` → entry point `main.run`, and `render_files` emits `from main import run`. `main.run` does not exist → **ImportError on DRUM startup**, no warning. Also sweeps in closures, `@staticmethod`, and code under `if False:`.

### C12. Eval reports 5/5 pass for agents that produce nothing [VERIFIED]
`pipeline/evaluator.py:36-37`, `:178-197`

`_DIRECT_RUNNER` coerces any non-dict into `{"response": str(result)}`, so `None` → `"None"`, satisfying an `output_schema` of `{"response":"str"}`.
```
returns_None        pass=5/5   returns_empty_str   pass=5/5
swallows_exception  pass=5/5   returns_error_dict  pass=5/5  ('ERROR: could not reach LLM gateway')
```
`_evaluate_output:187` short-circuits to pass when `output_schema` is falsy — and it defaults to `{}` on the LLM path. Eval proves only "a subprocess exited 0 and printed something."

### C13. `not-a-package` uses `and`, so a package missing `custom.py` validates clean [VERIFIED]
`pipeline/gap_analysis.py:59-67`

Requires **all three** of `custom.py`/`.env.template`/`pyproject.toml` to be absent. A bundle with no DRUM entry point counts as "a package", and cascades: the runtime-param rule at `:97` also needs all three, so a missing `custom.py` silently disables that too. A lone `pyproject.toml` containing `# nothing` → clean.

### C14. Nothing ever parses the generated Python [VERIFIED]
`pipeline/gap_analysis.py:69-80`

No `ast.parse`, no import resolution, no check the entry function exists.
```
custom.py = "def load_model(:\nthis is not python at all ###"   → findings=[], blocking=False
custom.py importing a module absent from the bundle             → clean
```

### C15. `_extract_dependencies` regex mis-reads real pyprojects, disabling the rule [VERIFIED]
`dr/platform_rules.py:69`

Unanchored non-greedy `dependencies\s*=\s*\[(.*?)\]`:
```
["uvicorn[standard]>=1","langgraph",...]  -> set()      # stops inside the extras bracket
[tool.uv] dev-dependencies=[...] first    -> {'pytest'} # matches the wrong array
[tool.poetry.dependencies]                -> set()      # all Poetry projects
```
Empty `orig_deps` ⇒ `removed` empty ⇒ **reports clean by construction.** Verified end-to-end: generated package dropped langgraph+openai and passed.

### C16. Generated `pyproject.toml` is not valid TOML [VERIFIED]
`templates/pyproject_toml.j2:7` — `"{{ dep }}",` interpolates raw. Combined with C1 the deps contain embedded quotes → `tomllib.loads` → `Unclosed array`. Independent of C1, any dep containing `"` corrupts the file. Needs `| tojson`.

### C17. LLM-generated `agent_purpose` escapes the module docstring → SyntaxError [VERIFIED]
`templates/myagent_*.j2:1` — `"""{{ agent_purpose }} — ..."""` with unsanitized free-form LLM text.
```
agent_purpose='Uses """ triple quotes """ in docs'  →  SyntaxError: invalid syntax (line 1)
```
`custom.py`'s `from myagent import MyAgent` then can't import — the deployment cannot start. A `\` in the purpose also emits `SyntaxWarning` and mangles text.

### C18. Comma-bearing type annotations shred the Workload service → HTTP 500 on every request [VERIFIED]
`models/agent_config.py:10,19` + `templates/workload_service_py.j2:16-17`

`_SIGNATURE` regex + naive `.split(",")`:
```
"def run_agent(payload: dict[str, str], top_k: int = 3)" -> ['payload', 'str]', 'top_k']
→ run_agent() got an unexpected keyword argument 'str]'   → 500 on every /invoke
```

### C19. `shell_launcher` resolves the node entrypoint from CWD → code execution from an untrusted repo [VERIFIED]
`shell_launcher.py:26-35` → `cli.py:115`

Falls back to `Path.cwd()/shell/dist/cli.js`, then `os.execvp`s it. A repo shipping `shell/dist/cli.js` executes as the user the moment they `cd` in and run `superrobot` bare — and ingesting untrusted third-party repos is this tool's entire job.

### C20. Receipt id is interpolated into a path with no validation — traversal [VERIFIED]
`pipeline/receipts.py:47,54`
```
load_receipt('../../secret', base) → reads /tmp/srtest/secret.json
save_receipt(Receipt(id='../../pwned')) → wrote /private/tmp/srtest/pwned.json
```
Reachable directly from `receipt show|diagnose|replace` user arguments (`cli.py:758,811,832`).

### C21. `--json` emits non-JSON on every error path [VERIFIED]
`cli.py:337,398,403,624,632,639,713`
```
$ superrobot validate /nonexistent --json   →  "Not a directory /nonexistent"  (not JSON)
```
Same bug class already fixed once at `cli.py:577-580` (which even documents it) but never applied to error branches. The Pi shell parses this stdout as JSON, so users see a parse failure instead of "Not authenticated". The correct pattern already exists in-file at `:768`.

---

## IMPORTANT

**API / deploy**
- **I1** `workload_client.py:41-49`, `memory_client.py:41-49` — list responses assume one page; `next`/`totalCount` dropped, no `limit`/`offset`. Past one page, existing resources aren't found → C7's create path. Also unclear `?name=` is even a supported filter. [VERIFIED]
- **I2** `workload_client.py:42-43` — `name` interpolated into the query string unencoded. `'a&limit=1'` injects a param; `'rel#2'` truncates at the fragment. Names derive from user repo/dir names. [VERIFIED]
- **I3** `workload_client.py:59-61` — empty workload id builds `PATCH .../workloads//`; many gateways normalize `//` → PATCH against the **collection**. No non-empty guard, and `create`/`replace` return `success=True` with a blank id if `id` is absent. [VERIFIED]
- **I4** `llm_gateway.py:149,167,205` — unguarded `choices[0]`; Azure routinely returns empty `choices` on content-filter. `IndexError` → swallowed by `:131`'s bare except → 4 retries / 14s → silent fallback. [VERIFIED]
- **I5** `llm_gateway.py:131-135,177-181` — catch-all retry re-issues 401/400 identically to 503, stacked on the SDK's own retries (worst case 12 requests, ~14s) then silently degrades. A bad token is never reported. [VERIFIED]
- **I6** `llm_gateway.py:161-169` — stream never closed, `AsyncOpenAI` client never `aclose`d; every `LLMGateway()` leaks an httpx pool. [INSPECTION]
- **I7** No client observes the workload lifecycle (`submitted→provisioning→launching→running`); `deploy_workload` returns success the moment the API accepts the manifest, before the container serves or crash-loops. [INSPECTION]
- **I8** `workload_client.py`/`memory_client.py` — no 429 handling despite the vendored spec calling for backoff. Retry belongs on the GET only (POST retry would duplicate). [INSPECTION]

**CLI / orchestration**
- **I9** `cli.py:320-321` — `transform` exits 0 when eval fails 5/5; `eval_summary` never consulted, and human output never mentions eval. [VERIFIED]
- **I10** `cli.py:872-882` — `receipt replace` can never replay an `--artifact-id` deploy (passes only `image_uri`), so the entire Code-to-Workload path is unreplayable — the command's whole purpose. [VERIFIED]
- **I11** `cli.py:406-413` — `--target agent-app` silently accepts and discards `--image-uri`/`--artifact-id`/`--secret`, exit 0; receipt records nulls. `--has-ui` symmetrically ignored on workload. [VERIFIED]
- **I12** `cli.py:327-330` — a wrong/stale `--source` path silently downgrades `validate` to the weak rule set and reports success. Needs `exists=True`. [VERIFIED]
- **I13** `cli.py:76-89` — a mistyped subcommand launches the interactive shell; in CI that's a misleading "shell not found" for a typo. `_KNOWN_SUBCOMMANDS` duplicates the command registry. [VERIFIED]
- **I14** `cli.py:583-590` — `deploy --json` cannot tell you a blocking finding was waived (no `gap_summary`, no `waived_findings`). [VERIFIED]
- **I15** `cli.py:516` — `--waive` also bypasses the structural `not-a-package` precondition, firing a real deploy against an arbitrary directory. [VERIFIED]
- **I16** `cli.py:629-635` — `--secret` accepts empty keys (`=v`) and silently drops duplicates. [VERIFIED]

**Analysis correctness**
- **I17** `scanner.py:242,252` — multiple frameworks: silent last-writer-wins, ordered by `rglob`. Identical repos get different base classes on different machines. [VERIFIED]
- **I18** `scanner.py:70-82,275-279` — `FRAMEWORK_SYMBOLS["Pipeline"] → haystack` fires on `sklearn.pipeline.Pipeline` at 0.90. No import gating despite a comment claiming otherwise. [VERIFIED]
- **I19** `scanner.py:253-256` — `has_state_graph` fires on *any* identifier named `StateGraph`; a local class flips langchain→langgraph at conf 1.00. [VERIFIED]
- **I20** `scanner.py:164-167` — dependency→framework via naive substring: `my-langchain-migration-notes` → langchain. [VERIFIED]
- **I21** `scanner.py:480-505` — Poetry pyprojects yield **zero** deps (deployed bundle has no framework); single-line `dependencies = [...]` yields garbage and hard-crashes generation. [VERIFIED]
- **I22** `scanner.py:484-487` — pip directives emitted as PEP 621 deps, **including `--index-url` credentials copied into the uploaded artifact**; `-r` never followed. [VERIFIED]
- **I23** `schema_inference.py:75-81` — positional-only params dropped from the input schema → generated call site raises `TypeError`. `*args/**kwargs` invents a `query` param. [VERIFIED]
- **I24** `schema_inference.py:91-97` — every `Subscript` treated as `list`; `Optional[dict]` → `list[optional]`, `int | None` → `int|str`. These become the deployed artifact's declared contract. [VERIFIED]

**Validation gate**
- **I25** `platform_rules.py:9,20-27` — `NESTED_IMPORT_PATTERN` catches one of four DRUM-breaking import forms; misses `import agent.agent.x`, `from agent.agent import x as y`, relative imports, `importlib`. Only `agent/agent/**` is scanned. [VERIFIED]
- **I26** `gap_analysis.py:99,108` — runtime-param check is a raw substring test satisfied by a comment mention; and severity is `warning` for what is a guaranteed runtime credential failure. [VERIFIED]
- **I27** `gap_analysis.py:71,177` — `read_text()` with no encoding/try raises **out of the deploy gate** (`cli.py:515` doesn't wrap it) → traceback, no receipt. [VERIFIED]
- **I28** Rule-coverage gaps vs the vendored authority: no check for `AGENTS.md`, workload Dockerfile/yaml presence, Python `>=3.11`, dependency resolvability (`dr dependency check` is a documented hard stop), or that imported modules exist in the flat bundle. [INSPECTION]
- **I29** `framework_mapper.py:31` — `pydantic_ai` maps at **0.95** to a DR framework that doesn't exist (canonical list is `langgraph, crewai, llamaindex, nat, base`). [INSPECTION]
- **I30** `framework_mapper.py:43-46` — no `base`/generic target, so "unknown" answers `LANGGRAPH` confidently. `map_framework("nat")` → `(LANGGRAPH, 0.3)`; it only works via an unrelated short-circuit. [VERIFIED]
- **I31** `analyzer.py:45-62` — `_needs_confirm` is a hand-copied duplicate of `_LANGGRAPH_FALLBACKS`; frameworks in neither (dspy, agno) skip the "Confirm before generate" prompt despite being 0.3-confidence guesses. [VERIFIED]
- **I32** `analyzer.py:28-32` — the LLM path bypasses every fallback safeguard: no `map_framework` sanity check, no confidence clamp, no `_missing_requirements`, no `infer_schemas`, and **drops `scan.risk_flags`** entirely. Strictly weaker than the fallback, with no signal. [INSPECTION]
- **I33** `models/analysis_result.py:29` — no `validate_assignment`, so `ge/le` is construction-only. `engine/pipeline.py:110` exploits this to write `confidence = max(..., 0.7)`, deliberately erasing the low-confidence signal. [VERIFIED]

**Codegen**
- **I34** `workload_Dockerfile.j2:5` — `pip install {{ dependencies | join(" ") }}` unquoted: `>=` is parsed as shell redirection. Verified to create files named `=0.2`, `=2.0`; installs unpinned and swallows pip output. Also a shell-injection sink from scanned content. [VERIFIED]
- **I35** `myagent_*.j2:11`, `workload_service_py.j2:12` — `entry_module` never validated as an identifier. A repo with `my-agent.py` → `from my-agent import ...` → SyntaxError; the migrated file is unimportable by any name. [VERIFIED]
- **I36** `workload_service_py.j2:16` — empty `entry_params` silently discards the whole request payload: **HTTP 200 with a plausible input-free answer**. The DRUM path diverges (500), so the same agent behaves differently by entry point. [VERIFIED]
- **I37** `config_generator.py:216` + `engine/pipeline.py:68` — every bundle is named `my-agent`; two migrations into one DR org collide. [VERIFIED]
- **I38** `infra_agent_py.j2:3-4` — generated `pyproject.toml` omits `pulumi`/`pulumi_datarobot` → `ModuleNotFoundError` on `pulumi up`. [VERIFIED]
- **I39** `myagent_*.j2:24` — `PROMPT_TEMPLATE_ID` is documented as required in `.env.template`/`AGENTS.md` but never wired as a runtime param → `KeyError` on first use. [VERIFIED]
- **I40** `dr_llm_py.j2:103-196` — every model silently swapped to the DR default (`ChatOpenAI(model="gpt-4o")` → `azure/gpt-5-5-...`), and Anthropic/Vertex/Bedrock clients get an OpenAI-shaped `base_url` they can't speak. Migrated non-OpenAI agents are broken by construction. [VERIFIED logic / INSPECTION wire]

**Setup / auth / state**
- **I41** `setup/config.py:70-71` — token written then chmod'd; observed at `0o644` with content present. If `chmod` raises, it stays world-readable. [VERIFIED]
- **I42** `setup/config.py:23` — config dir created `0o755` and never tightened; a pre-existing `0o777` dir is trusted. [VERIFIED]
- **I43** `setup/config.py:70` — `write_token_env` follows symlinks and is non-atomic (bare truncating `write_text`, no fsync), unlike `save_state` which does temp+rename. [VERIFIED]
- **I44** `pipeline/receipts.py:48` — receipts written `0644` in a `0755` dir, non-atomic; combined with unsanitized `error_message` they're a plausible secret sink. [VERIFIED]
- **I45** `setup/endpoints.py:28-32` — no host/scheme validation. `'javascript:alert(1)'` → `'https://javascript:alert(1)/api/v2'`; `/api/v2/api/v2` duplicated silently. The bearer token is then POSTed to it. [VERIFIED]
- **I46** `setup/endpoints.py:30-31` — `http://` accepted and preserved; token sent in cleartext with no warning. [VERIFIED]
- **I47** `setup/doctor.py:70-71` + `cli.py:199-208` — `status` reports **ready** with a completely invalid token (`skip_gateway=True` records `llm_gateway: True "Skipped"` and counts it toward ready). `setup --skip-gateway` exits ready having verified nothing. [VERIFIED]
- **I48** `setup/doctor.py:77` — `httpx.ConnectError` isn't a `GatewayError`, so it escapes the handler; `probe_capabilities` has none at all. A flaky VPN produces a raw traceback from `doctor`/`status`. [VERIFIED]
- **I49** `setup/probes.py:54-60` — any non-`{200,201,403}` (500/502/503/timeout) is persisted as **not entitled**, producing a permanent-looking, misleading entitlement error that `doctor` cannot clear. [VERIFIED]
- **I50** `setup/probes.py:57,60,87` — inverse: `403` counted as *entitled*, so a genuinely forbidden account proceeds and fails later opaquely. [INSPECTION]
- **I51** `setup/runner.py:49,79` — `EndpointError`/`GatewayError` uncaught; a mistyped URL or bad token exits the wizard with a stack trace. [VERIFIED]
- **I52** `setup/config.py:48` + `models.py:59-60` — corrupt/partial/old-schema state → `JSONDecodeError`/`KeyError`/`ValueError` traceback from `doctor`/`status`. `endpoint: null` is silently accepted as the string `"None"`. [VERIFIED]
- **I53** `pipeline/receipts.py:64` — one malformed receipt breaks *every* receipt command (no per-file try); recovery requires manually deleting files. [VERIFIED]
- **I54** `doctor.py:95-100` — `run_doctor` builds a fresh `SetupState` and **never persists it**, so "run `superrobot doctor` to re-probe capabilities" (`cli.py:643,717`) is dead advice. [VERIFIED]

---

## MINOR / NIT (abbreviated)

`read_text()` without encoding at 13 sites (M1) · `scanner.py:484,491` missing the `OSError` guard its own file uses elsewhere (M2) · three divergent directory-exclusion lists, so `config_generator` migrates files `scanner` never analyzed (M3) · `git clone` has no timeout (M4) · cloned repos never cleaned up, and `transform <url>` writes output *into* the temp clone that gets reaped (M5) · timed-out eval subprocesses orphaned, ×5 (M6) · evaluator runs `.venv/bin/python` from the untrusted repo with full `os.environ` (M7) · Rich markup injection from user paths crashes `validate` (M8) · unparseable files dropped silently with no risk flag (M9) · `SECRET_PATTERNS` miss real AWS/GitHub/Anthropic keys while firing on `"TODO"` (M10) · `_extract_stategraph` not gated on langgraph — networkx hijacks the graph (M11) · `agents.agent` → openai_agents collides with local `agents/` packages (M12) · `NESTED_IMPORTS` fires on a string literal (M13) · no dedupe in `_read_dependencies` (M14) · `src/main.py` gets no filename bonus (M15) · `LATENCY_LIMIT_MS` is both timeout and threshold, measured across both attempts → false "timeout" (M16) · `estimated_cost_usd` hardcoded 0.004 (M17) · `EvalSummary` has no consumer in the deploy gate — 0/5 doesn't block (M18) · "5-shot eval" is one shot run five times (M19) · `validate_endpoint_usage` requires literal `dr.Client()` (M20) · `_extract_dependencies` handles only `==`/`>=`, so `~=` false-blocks and a `>=0.5`→`==0.0.1` downgrade passes (M21) · `_parse_runtime_keys` mishandles `export` (M22) · `.env` parser keeps quotes and mishandles `export` (M23) · receipts grow unbounded, `list_receipts` validates all on every call (M24) · default model literal duplicated in 6 places (M25) · `--framework` validated only after scan+analyze, raw traceback (M26) · `scan`/`generate` traceback on a bad source path, `--json` ignored (M27) · `receipt operations --target` unvalidated, exits 0 on a typo (M28) · `-h` advertised but rejected (M29) · `AGENTS.md` env table renders as a broken GFM table (M30) · duplicate keys in `.env.template` with last-wins-empty (M31) · `agent_name` unquoted in 4 output formats (latent) (M32) · `input_schema`/`output_schema` computed but referenced by no template (M33) · token with a newline injects arbitrary `.env` vars (M34) · `run_setup` mutates global `os.environ` (M35) · prediction-server guard wrong in both directions (M36) · `dr auth check` unbounded and endpoint-blind (M37) · `gateway.py` accepts any 200 as verified, incl. a captive portal (M38) · `waived=bool(gap_report.blocking)` infers the flag (M39) · `skip_clone` dead parameter (M40) · `on_stage` callback unused so long transforms print nothing (M41) · `generate` has no `--json` (M42).

---

## F1. Root cause of the known flaky test [VERIFIED]

`tests/unit/test_cli.py::test_memory_ensure_blocked_without_auth` passes alone, fails in a full run. Minimal 2-test repro:
```
pytest tests/unit/engine/test_pipeline.py::test_transform_engine_headless_generate \
       tests/unit/test_cli.py::test_memory_ensure_blocked_without_auth
→ .F  AssertionError: 'Not authenticated' not in 'Memory API not entitled...'
```
**Not** an `lru_cache` — it's `os.environ`, poisoned by C6. Two independent defects, either of which alone fixes it:
1. `dr/llm_gateway.py:32` mutates global `os.environ` from the user's home dir with no scoping or cleanup.
2. `cli.py:429-441 _resolve_credentials` falls back to `os.environ` **even when `--config-dir` is given**. `setup/doctor.py:30,35` gets this right (`"" if config_root is not None else os.environ.get(...)`), so the docstring claiming "same resolution order as doctor" is factually wrong.

Mechanism: with creds leaked, the `cli.py:712` auth guard is skipped; control reaches `:715` where `state` is still `None` → `:717` prints "not entitled".

---

## Cleared (checked, found safe)

No `shell=True`, no `os.system` — every subprocess uses `create_subprocess_exec` with argv lists · `repo.py` reconstructs GitHub URLs from regex captures rather than passing input through, so `--upload-pack`-style injection is blocked · no `pickle`/`marshal`/bare `yaml.load`; `yaml.safe_load` used · HTTP status is checked before the body is trusted on all **write** paths, and `except ValueError` correctly catches `JSONDecodeError` · `gateway_base_url` verified correct — the previously-shipped `/v1` bug has not regressed · `--image-uri`/`--artifact-id` mutual exclusion enforced in both layers · `--waive` receipt recording correct in both branches · all five `myagent_*.j2` agree on the interface `custom.py` depends on · no template references a variable missing from `ctx` · all `asyncio.run` sites are top-level with no nesting; no unawaited coroutines · `json_mode` safe as currently used.

---

## Coverage gaps in this review

Stated so this isn't mistaken for exhaustive: no fuzz/property testing of `scanner` beyond hand-built adversarial repos · `pulumi_datarobot` provider surface unverified (package not installable here) · `dr_llm.py` non-OpenAI wire failures reasoned, not observed against a live gateway · no review of the test suite's own quality/coverage · no concurrency/race testing beyond inspection · three of the five planned waves were not run (see below).

---

## Triage order

1. **C1, C3** — fire on the default happy path; C3 destroys user data.
2. **C2, C4, C5, C7** — silently-wrong deploys and a broken audit trail.
3. **C6 / F1** — the test suite spends a real token and leaks it process-wide.
4. **C8–C12, C16–C18** — wrong analysis → broken generated artifacts.
5. **C13–C15** — the deploy gate reports clean on genuinely broken packages.
6. **C19, C20, C21** — code execution, path traversal, JSON purity.
7. Importants by subsystem, then Minors.
