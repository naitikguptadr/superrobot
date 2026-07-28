# IR Migration — Phase 1 (Vertical Slice) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Prove the IR architecture end to end on one genuinely complex agent — CPG dataflow → Migration IR → `agent_spec.md` → DataRobot recipe scaffold → verified implementation — before building breadth.

**Spec:** `docs/superpowers/specs/2026-07-28-ir-based-migration-architecture.md`

**Sequencing rationale:** a thin slice through every layer surfaces architectural problems while they're still cheap. Breadth (more frameworks, more probes, equivalence replay) comes in Phase 2, only once the spine holds.

**Non-goals for Phase 1:** differential equivalence replay · interprocedural dataflow · multi-agent topology extraction · deleting the CLI (that lands in Phase 3 once the new path demonstrably works — removing the only working interface before the replacement is proven would be reckless).

---

### Task 1: Dataflow probe — resolve values reaching a call parameter

**Files:**
- Create: `superrobot/pipeline/graph/dataflow.py`
- Test: `tests/unit/pipeline/graph/test_dataflow.py`

This is the highest-leverage single addition: it turns the config-driven LLM-client case from "unsolvable by regex" into a deterministic answer.

- [ ] **Step 1: Write the failing test**

```python
"""Reaching-definitions for values that flow into call parameters."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.dataflow import resolve_parameter_values


def _repo(tmp_path: Path, source: str) -> Path:
    (tmp_path / "main.py").write_text(source)
    return tmp_path


def test_resolves_a_literal_argument(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'llm = ChatOpenAI(model="gpt-4o")\n')

    values = resolve_parameter_values(build_repo_graph(repo), "ChatOpenAI", "model")

    assert values.resolved == ["gpt-4o"]
    assert values.unresolved == []


def test_resolves_through_a_local_variable(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'name = "gpt-4o"\nllm = ChatOpenAI(model=name)\n')

    values = resolve_parameter_values(build_repo_graph(repo), "ChatOpenAI", "model")

    assert values.resolved == ["gpt-4o"]


def test_resolves_through_an_aliased_class(tmp_path: Path) -> None:
    """The case regex provably cannot handle."""
    repo = _repo(tmp_path, 'CLS = ChatOpenAI\nllm = CLS(model="gpt-4o")\n')

    values = resolve_parameter_values(build_repo_graph(repo), "ChatOpenAI", "model")

    assert values.resolved == ["gpt-4o"]


def test_reports_an_unresolvable_value_rather_than_guessing(tmp_path: Path) -> None:
    """A value from runtime config cannot be known statically. It must be
    reported as unresolved -- never silently omitted, never guessed.
    """
    repo = _repo(tmp_path, 'import os\nllm = ChatOpenAI(model=os.environ["MODEL"])\n')

    values = resolve_parameter_values(build_repo_graph(repo), "ChatOpenAI", "model")

    assert values.resolved == []
    assert values.unresolved, "an unresolvable value must be reported explicitly"
    assert "MODEL" in values.unresolved[0].expression


def test_every_finding_carries_provenance(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'llm = ChatOpenAI(model="gpt-4o")\n')

    values = resolve_parameter_values(build_repo_graph(repo), "ChatOpenAI", "model")

    assert values.sites, "must record where each value was found"
    site = values.sites[0]
    assert site.file.endswith("main.py")
    assert site.line == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/pipeline/graph/test_dataflow.py -v`
Expected: `ModuleNotFoundError: superrobot.pipeline.graph.dataflow`

- [ ] **Step 3: Implement**

Create `superrobot/pipeline/graph/dataflow.py` with:
- `@dataclass Site` — `file: str`, `line: int`, `node_id: str` (provenance for every fact)
- `@dataclass Unresolved` — `expression: str`, `site: Site`, `reason: str`
- `@dataclass ParameterValues` — `resolved: list[str]`, `unresolved: list[Unresolved]`, `sites: list[Site]`
- `resolve_parameter_values(repo_graph, callable_name, parameter) -> ParameterValues`

Implementation shape: walk each module's AST (reuse the parsed trees from `builder`); find `ast.Call` nodes whose target resolves to `callable_name` (resolve aliases by tracking `Name -> Name` assignments and `import ... as` within the module scope — intraprocedural only, per the spec's non-goals); for the named keyword argument, resolve `ast.Constant` directly and single-assignment local variables by reaching definition; anything else becomes an `Unresolved` carrying `ast.unparse` of the expression and a reason.

The unresolved path is not a fallback — it is a first-class output. Silently omitting an unresolvable value is the failure mode this whole architecture exists to prevent.

- [ ] **Step 4: Verify it passes**

Run: `uv run pytest tests/unit/pipeline/graph/test_dataflow.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add superrobot/pipeline/graph/dataflow.py tests/unit/pipeline/graph/test_dataflow.py
git commit -m "feat: dataflow probe resolving values that reach call parameters"
```

---

### Task 2: LLM call-site probe with a coverage ledger

**Files:**
- Create: `superrobot/pipeline/probes/llm_calls.py`
- Create: `superrobot/pipeline/probes/__init__.py`
- Test: `tests/unit/pipeline/probes/test_llm_calls.py`

Finds every LLM invocation *including ones we have no shim for*, so the ledger can account for them.

- [ ] **Step 1: Write the failing test**

```python
"""LLM call-site discovery -- must find sites it cannot handle, not skip them."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.probes.llm_calls import find_llm_call_sites


def _repo(tmp_path: Path, source: str) -> Path:
    (tmp_path / "main.py").write_text(source)
    return tmp_path


def test_finds_a_known_client_and_resolves_its_model(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'llm = ChatOpenAI(model="gpt-4o")\n')

    sites = find_llm_call_sites(build_repo_graph(repo))

    assert len(sites) == 1
    assert sites[0].client == "ChatOpenAI"
    assert sites[0].model == "gpt-4o"
    assert sites[0].known is True


def test_finds_an_aliased_client(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "CLS = ChatOpenAI\nllm = CLS(model='gpt-4o')\n")

    sites = find_llm_call_sites(build_repo_graph(repo))

    assert [s.client for s in sites] == ["ChatOpenAI"]


def test_reports_an_unknown_client_instead_of_ignoring_it(tmp_path: Path) -> None:
    """The governing invariant: a provider we have no shim for must still be
    surfaced, so the coverage ledger can block on it.
    """
    repo = _repo(tmp_path, 'llm = ChatFireworks(model="llama-v3")\n')

    sites = find_llm_call_sites(build_repo_graph(repo))

    assert len(sites) == 1
    assert sites[0].client == "ChatFireworks"
    assert sites[0].known is False


def test_does_not_match_inside_a_string_literal(tmp_path: Path) -> None:
    """The regex implementation rewrote the name inside strings."""
    repo = _repo(tmp_path, 'log("we call ChatOpenAI(...) here")\n')

    assert find_llm_call_sites(build_repo_graph(repo)) == []


def test_every_site_carries_provenance(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'llm = ChatOpenAI(model="gpt-4o")\n')

    site = find_llm_call_sites(build_repo_graph(repo))[0]

    assert site.site.file.endswith("main.py")
    assert site.site.line == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/pipeline/probes/test_llm_calls.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Create `superrobot/pipeline/probes/__init__.py` (empty) and `superrobot/pipeline/probes/llm_calls.py`:
- `@dataclass LlmCallSite` — `client: str`, `model: str | None`, `params: dict[str, str]`, `known: bool`, `site: Site`
- `find_llm_call_sites(repo_graph) -> list[LlmCallSite]`

Detection is AST-based (never regex over text). A call is an LLM call site if its resolved callable name is in `engine.providers.LLM_CLIENT_SHIMS` (`known=True`) **or** matches an open heuristic for unknown providers (`known=False`) — e.g. a constructor whose name starts with `Chat`, or a call resolving into a module whose top-level package is a known LLM SDK. Resolve `model` via `dataflow.resolve_parameter_values`.

Being generous with `known=False` is deliberate: a false positive costs one line in a report, a false negative ships a broken agent.

- [ ] **Step 4: Verify it passes**

Run: `uv run pytest tests/unit/pipeline/probes/test_llm_calls.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add superrobot/pipeline/probes/ tests/unit/pipeline/probes/
git commit -m "feat: AST-based LLM call-site probe that surfaces unknown providers"
```

---

### Task 3: Migration IR schema and the coverage ledger

**Files:**
- Create: `superrobot/ir/model.py`
- Create: `superrobot/ir/ledger.py`
- Create: `superrobot/ir/__init__.py`
- Test: `tests/unit/ir/test_ledger.py`

- [ ] **Step 1: Write the failing test**

```python
"""The coverage ledger -- no source fact may go unaccounted for."""

from __future__ import annotations

import pytest

from superrobot.ir.ledger import CoverageLedger, LedgerError
from superrobot.ir.model import Disposition, SourceFact


def _fact(fact_id: str) -> SourceFact:
    return SourceFact(id=fact_id, kind="llm_call", description=fact_id, file="main.py", line=1)


def test_a_fully_accounted_ledger_is_clean() -> None:
    ledger = CoverageLedger([_fact("a"), _fact("b")])
    ledger.record("a", Disposition.MIGRATED)
    ledger.record("b", Disposition.DEFERRED, reason="user opted out")

    assert ledger.is_clean()
    assert ledger.blocking() == []


def test_an_unaccounted_fact_blocks() -> None:
    """The governing invariant: silence is impossible."""
    ledger = CoverageLedger([_fact("a"), _fact("b")])
    ledger.record("a", Disposition.MIGRATED)

    assert not ledger.is_clean()
    assert [f.id for f in ledger.unaccounted()] == ["b"]


def test_a_blocking_disposition_blocks() -> None:
    ledger = CoverageLedger([_fact("a")])
    ledger.record("a", Disposition.BLOCKING, reason="no shim for this provider")

    assert not ledger.is_clean()
    assert [f.id for f in ledger.blocking()] == ["a"]


def test_deferring_without_a_reason_is_rejected() -> None:
    """A deferral with no explanation is indistinguishable from silence."""
    ledger = CoverageLedger([_fact("a")])

    with pytest.raises(LedgerError):
        ledger.record("a", Disposition.DEFERRED)


def test_recording_an_unknown_fact_is_rejected() -> None:
    ledger = CoverageLedger([_fact("a")])

    with pytest.raises(LedgerError):
        ledger.record("nonexistent", Disposition.MIGRATED)


def test_the_report_names_every_gap() -> None:
    ledger = CoverageLedger([_fact("a"), _fact("b")])
    ledger.record("a", Disposition.BLOCKING, reason="unsupported provider")

    report = ledger.report()

    assert "unsupported provider" in report
    assert "b" in report, "an unaccounted fact must appear in the report"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/ir/test_ledger.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`superrobot/ir/model.py` — pydantic models with an `Evidence` type (`file`, `line`, `node_id`) required on every fact-bearing element: `SourceFact`, `Disposition` (`StrEnum`: `MIGRATED`/`DEFERRED`/`BLOCKING`), `EntryPoint`, `Tool`, `LlmCall`, `Orchestration`, `StateItem`, `ExternalIO`, `ConfigVar`, `Residue`, and the top-level `MigrationIR`.

`superrobot/ir/ledger.py` — `LedgerError`, and `CoverageLedger` with `record(fact_id, disposition, reason=None)` (rejects unknown ids and reasonless deferrals/blocks), `unaccounted()`, `blocking()`, `is_clean()`, `report()`.

- [ ] **Step 4: Verify it passes**

Run: `uv run pytest tests/unit/ir/test_ledger.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add superrobot/ir/ tests/unit/ir/
git commit -m "feat: Migration IR schema and coverage ledger"
```

---

### Task 4: Project the Migration IR into DataRobot's `agent_spec.md`

**Files:**
- Create: `superrobot/ir/agent_spec.py`
- Test: `tests/unit/ir/test_agent_spec.py`

- [ ] **Step 1: Read the authoritative format first**

Read `vendor/datarobot-agent-skills/skills/datarobot-agent-assist/` for the real `agent_spec.md` shape before writing the projection — the fields are `model`, `system_prompt`, `tools[{function_name, inputs[{arg_name,type,object_schema}], out[{arg_name,type}], auth_spec}]`, `examples`, `frontend.type`. Confirm against the skill, don't trust this plan's summary.

- [ ] **Step 2: Write the failing test**

Test that a populated `MigrationIR` projects to YAML that: contains the resolved model; contains one `tools` entry per IR tool with matching `function_name`, input arg names and types; round-trips through `yaml.safe_load`; and — critically — **raises rather than emitting a spec when the ledger is not clean**, because a spec generated from an incomplete understanding is exactly the silently-wrong output this architecture exists to prevent.

- [ ] **Step 3: Run to verify it fails**, then implement `migration_ir_to_agent_spec(ir) -> str`, then verify it passes.

- [ ] **Step 4: Commit**

```bash
git add superrobot/ir/agent_spec.py tests/unit/ir/test_agent_spec.py
git commit -m "feat: project Migration IR into DataRobot agent_spec.md"
```

---

### Task 5: Wrap the DataRobot scaffold scripts

**Files:**
- Create: `superrobot/dr/scaffold.py`
- Test: `tests/unit/dr/test_scaffold.py`

Thin, tested wrappers around `clone_template.py`, `select_framework.py`, `setup_template.py` from the vendored skill — **called, never reimplemented.**

- [ ] **Step 1: Locate and read the real scripts**

`vendor/datarobot-agent-skills/skills/datarobot-agent-assist/scripts/`. Confirm each script's actual CLI signature before wrapping (`--target-dir`, `--framework`, `--llm-model`); the plan's summary is not authoritative.

- [ ] **Step 2: Write failing tests** covering: each wrapper invokes the right script with the right args (assert on a fake runner, no real subprocess); a non-zero exit surfaces a clear error rather than silently continuing; and `select_framework` rejects a framework outside the real supported set (`langgraph`, `crewai`, `llamaindex`, `nat`, `base`).

- [ ] **Step 3: Run to verify failure**, then implement `clone_template(target_dir)`, `select_framework(target_dir, framework)`, `setup_template(target_dir, llm_model)` with an injectable runner, then verify.

- [ ] **Step 4: Commit**

```bash
git add superrobot/dr/scaffold.py tests/unit/dr/test_scaffold.py
git commit -m "feat: wrap DataRobot scaffold scripts instead of reimplementing them"
```

---

### Task 6: Expose the pipeline as Pi harness tools

**Files:**
- Modify: `shell/extensions/superrobot/tools.ts`
- Create: `shell/extensions/superrobot/ir-bridge.ts`
- Test: `shell/extensions/superrobot/ir-bridge.test.ts`

- [ ] **Step 1** Read the existing `cli-bridge.ts` and `tools.ts` to match established patterns (`executionMode: "sequential"`, rail/web controller notification, the `runJson` error shape).

- [ ] **Step 2** Write failing tests for an `ir-bridge` exposing `index`, `extract`, `spec`, and `scaffold`, each returning parsed JSON and surfacing a non-zero exit as a structured error rather than a parse failure.

- [ ] **Step 3** Implement the bridge and register the tools, following the existing `promptGuidelines` convention so the harness agent knows when to call each.

- [ ] **Step 4** Run `cd shell && npm test && npm run typecheck` — all green.

- [ ] **Step 5: Commit**

```bash
git add shell/extensions/superrobot/
git commit -m "feat: expose IR pipeline as Pi harness tools"
```

---

### Task 7: Vertical-slice proof on a complex agent

**Files:** none (verification)

The gate on Phase 2. Use `tests/fixtures/langgraph_research_agent` (multi-module, real tool, transitive framework import) or a harder real agent if available.

- [ ] **Step 1** Build the CPG; confirm the dataflow probe resolves the model actually used.
- [ ] **Step 2** Run the LLM call-site probe; confirm **every** call site is found, including any with no shim, each with provenance.
- [ ] **Step 3** Extract a Migration IR; review it by hand against the source. Record honestly what it got wrong — this is the architecture's real report card.
- [ ] **Step 4** Confirm the ledger reconciles, and that an artificially-introduced unknown provider **blocks** rather than passing.
- [ ] **Step 5** Project to `agent_spec.md`; scaffold a real recipe via the DR scripts; confirm `.datarobot/` exists — the gap that broke the original deploy.
- [ ] **Step 6** Full gates: `uv run pytest -q`, `ruff check`, `ruff format --check`, `mypy superrobot/`.
- [ ] **Step 7** Write the findings into `docs/superpowers/reviews/` — what the architecture handled, what it missed, what Phase 2 must address. If the slice reveals the design is wrong, say so plainly rather than proceeding on momentum.

---

## Phase 2 (not this plan)

LLM interpretation layer with provenance validation · implementation writer + verify/repair loop · `rehearsal.py` and `dr dependency check` wired into verification · probes for tools, state, external I/O, orchestration topology · deploy lifecycle observation.

## Phase 3 (not this plan)

Delete the CLI, templates, `config_generator`, `ast_migrate` · differential equivalence replay · OTel monitoring onboarding.
