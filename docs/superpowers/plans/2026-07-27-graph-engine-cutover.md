# Graph Engine Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the graph-based analysis engine actually deliver value to users — it is currently 931 lines with 55 passing tests and **zero production call sites**, and on real repos it is **functionally inert**.

**Architecture:** Fix the one defect that makes the engine inert (entry-point resolution has no heuristic tier, so reachability never runs), then *enrich* the existing `ScanResult` with the three things a call graph genuinely knows better — rather than replacing `scanner.py`, which already handles 12 fields a graph adds nothing to.

**Tech Stack:** Existing — `jedi`, `networkx`, `libcst`, Python 3.11+, `pytest`.

**Supersedes:** the "Not in this plan" deferred sections of `docs/superpowers/plans/2026-07-24-graph-based-pipeline-engine.md` and the "Rollout strategy" section of `docs/superpowers/specs/2026-07-24-graph-based-pipeline-engine-design.md`.

---

## Audit findings that drove this plan

Measured, not assumed (reproductions in the audit run on 2026-07-27):

1. **Zero production call sites.** `grep` for `pipeline.graph` across `superrobot/` returns only the graph package itself and its tests. `cli.py` still routes `scan`/`transform` through `engine.pipeline.TransformEngine` → `pipeline/scanner.py`, and `validate` through `pipeline/gap_analysis.py`. The engine is dead code to every user.

2. **The engine is inert on every realistic repo.** `resolve_entry_point()` returned `None` for **all 9** test fixtures. It implements only two tiers — a `pyproject.toml` console script, and an `if __name__ == "__main__":` guard — and none of the 9 fixtures has either (they're agent libraries, not CLIs; verified: no `__main__` guard, no `pyproject.toml` in any of them). Its own docstring says tier 3 is "callers should fall back to the existing name/filename heuristic scoring in `superrobot.pipeline.scanner`" — but no caller exists, so that fallback was never written.

3. **Consequences of (2), all verified:** with `entry_point=None`, `detect_framework()` takes its `not reachable` branch, which treats *everything* as reachable. So the call graph is never consulted, `unreachable_warnings` was empty (0) for all 9 fixtures, and framework detection degenerates to "is this import present anywhere in the repo" — precisely what `scanner.py` already does more cheaply.

4. **Current confidence output is a regression, not an improvement.** The graph reports a flat `1.00` for 8 of 9 fixtures, versus scanner's graduated `0.85`/`0.90`/`0.95`/`1.00`. That gradation is real domain knowledge (`scanner._compute_confidence`'s per-framework base: langchain 0.75, haystack 0.8, autogen 0.85, langgraph/crewai/llamaindex 0.9). Flattening it to a constant discards information and asserts more certainty than the tool has.

5. **The graph does contain the right answer already.** Every fixture's graph has the correct function node (`main.run_agent`), and `scanner.py`'s existing heuristic already ranks it first. Only the wiring is missing.

**Conclusion:** the highest-leverage change is small — implement entry-point tier 3 — and only *then* is a cutover worth doing at all.

## Design decisions (and what was rejected)

- **Enrich `ScanResult`; do not replace `scanner.py`.** `ScanResult` has 15 fields. A call graph is genuinely better at 3 (entry-point selection, confidence weighting, and the new unreachable-import finding). The other 12 — `dependencies` (parsed from `requirements.txt`/`pyproject.toml`), `env_vars` (regex over `os.getenv`), `tools` (decorator scan), `llm_clients` (constructor scan), `risk_flags` (secret patterns), `python_file_count`, etc. — gain nothing from reachability analysis. *Rejected:* full replacement, which would mean reimplementing 12 fields for zero analytical benefit and high regression risk.
- **Enrichment must be conservative: never reduce confidence below scanner's.** The graph may *confirm* (raise) confidence when it proves a framework is reachable from a real entry point, and may *add* findings. It may not silently lower a score, so cutover cannot regress any existing behavior. *Rejected:* letting the graph overwrite confidence outright (that's the flat-1.00 regression above).
- **No feature flag.** Because enrichment is conservative by construction and gated by the 9-fixture regression test, an opt-in flag would add a code path nobody exercises. *Rejected:* `--graph` opt-in, as unnecessary indirection.
- **Deleting `scanner.py`/`ast_migrate.py`/`gap_analysis.py` stays out of scope.** They remain the base layer this plan enriches, so the earlier plan's "delete the old modules" item is now explicitly **not** a future step — it was predicated on full replacement, which this plan rejects on evidence.

---

### Task 1: Entry-point resolution tier 3 (the change that makes everything else work)

**Files:**
- Modify: `superrobot/pipeline/graph/entry_points.py`
- Test: `tests/unit/pipeline/graph/test_entry_points.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/pipeline/graph/test_entry_points.py`:

```python
def test_falls_back_to_heuristic_when_no_guard_or_console_script(tmp_path: Path) -> None:
    """The common real-world case: an agent library with no __main__ guard
    and no console script, but an obviously-named entry function. Before
    tier 3 existed this returned None, which made the whole reachability
    layer inert (detect_framework treats an empty reachable set as
    "everything is reachable").
    """
    (tmp_path / "main.py").write_text(
        "def helper():\n"
        "    return 1\n\n"
        "def run_agent():\n"
        "    return helper()\n"
    )
    repo_graph = build_repo_graph(tmp_path)

    assert resolve_entry_point(repo_graph) == "main.run_agent"


def test_heuristic_prefers_higher_priority_name(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def process():\n"
        "    return 1\n\n"
        "def run_agent():\n"
        "    return 2\n"
    )
    repo_graph = build_repo_graph(tmp_path)

    # scanner.ENTRY_PRIORITY ranks run_agent (100) above process (70).
    assert resolve_entry_point(repo_graph) == "main.run_agent"


def test_heuristic_returns_none_when_nothing_looks_like_an_entry_point(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text("def helper():\n    return 1\n")
    repo_graph = build_repo_graph(tmp_path)

    assert resolve_entry_point(repo_graph) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/pipeline/graph/test_entry_points.py -k heuristic -v`
Expected: the first two FAIL (currently return `None`); the third passes already.

- [ ] **Step 3: Implement tier 3**

In `superrobot/pipeline/graph/entry_points.py`, extend the import from scanner and add the fallback. Reuse scanner's existing tables verbatim — this is the same domain knowledge, applied to graph nodes instead of a raw AST walk:

```python
from superrobot.pipeline.scanner import ENTRY_POINT_NAMES, ENTRY_PRIORITY
```

Add this function, and call it as the final fallback in `resolve_entry_point()` (replacing the bare `return _resolve_main_guard_call(repo_graph)` with a chain that tries the heuristic when the guard trace comes back `None`):

```python
def _resolve_by_heuristic(repo_graph: RepoGraph) -> str | None:
    """Rank the graph's own function nodes by scanner.py's entry-point
    name conventions and return the best candidate.

    This is tier 3 of resolve_entry_point's documented priority order, and
    in practice it is the tier that fires most often: agent repos are
    typically libraries invoked by a framework, not CLIs, so they have
    neither a console script nor an `if __name__ == "__main__":` guard
    (verified: none of the 9 repos in tests/fixtures/ has either). Without
    this tier, resolve_entry_point returns None for such repos, and
    detect_framework's `not reachable` branch then treats every import as
    reachable -- silently disabling the entire reachability analysis.

    Deliberately reuses scanner.ENTRY_POINT_NAMES / ENTRY_PRIORITY rather
    than inventing a parallel ranking, so the graph path and the existing
    scanner agree on what "looks like an entry point" means.
    """
    graph = repo_graph.graph
    best: tuple[int, str] | None = None

    for node_id, attrs in graph.nodes(data=True):
        if attrs.get("kind") != "function":
            continue
        local_name = strip_collision_suffix(node_id).rsplit(".", 1)[-1]
        if local_name not in ENTRY_POINT_NAMES and not local_name.startswith("run_"):
            continue

        score = ENTRY_PRIORITY.get(local_name, 0)
        if local_name.startswith("run_"):
            score += 10
        # Mirror scanner._rank_entry_points' filename bonus.
        path = attrs.get("path", "")
        if Path(path).name in ("main.py", "app.py", "__main__.py", "agent.py"):
            score += 20

        # Tie-break on node id so the result is deterministic regardless of
        # graph insertion order (which follows filesystem enumeration).
        if best is None or (score, node_id) > (best[0], best[1]):
            best = (score, node_id)

    return best[1] if best else None
```

Add the needed imports at the top of the file (`from pathlib import Path`, and `strip_collision_suffix` alongside the existing `code_object_node_id` import from `builder`). Update `resolve_entry_point()`'s docstring so tier 3 is described as implemented here rather than as a caller's responsibility.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/pipeline/graph/test_entry_points.py -v`
Expected: all pass, including the three new ones.

- [ ] **Step 5: Confirm the engine is no longer inert**

Run:
```bash
uv run python3 -c "
from pathlib import Path
from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.entry_points import resolve_entry_point
for n in ['langchain_agent','langgraph_research_agent','crewai_agent','raw_async_agent']:
    rg = build_repo_graph(Path('tests/fixtures')/n)
    print(n, '->', resolve_entry_point(rg))
"
```
Expected: each prints a real node id (e.g. `main.run_agent`), **not** `None`. This is the whole point of the task — if any still print `None`, stop and investigate before continuing.

- [ ] **Step 6: Commit**

```bash
git add superrobot/pipeline/graph/entry_points.py tests/unit/pipeline/graph/test_entry_points.py
git commit -m "feat: implement entry-point heuristic tier so reachability analysis actually runs"
```

---

### Task 2: Re-baseline the fixture regression gate

**Files:**
- Modify: `tests/unit/pipeline/graph/test_fixtures_regression.py`

Task 1 changes fixture behavior by design: entry points now resolve, so reachability actually runs. The existing gate asserted `graph_confidence >= scanner_confidence`, which may now behave differently. This task re-establishes the gate against real post-fix behavior — without weakening it.

- [ ] **Step 1: Observe the new real behavior**

Run: `uv run pytest tests/unit/pipeline/graph/test_fixtures_regression.py -v`
Record which fixtures (if any) now fail and exactly how. Do **not** edit the test yet.

- [ ] **Step 2: Investigate every failure before touching the test**

For each failure, determine whether it is (a) a genuine regression introduced by Task 1, or (b) the gate encoding an assumption that Task 1 deliberately and correctly changed. If (a), fix the source, not the test. Only (b) justifies editing the assertion, and the edit must preserve the gate's purpose: **the graph path must never disagree with scanner.py on the detected framework, and must never report lower confidence than scanner.py.**

- [ ] **Step 3: Strengthen the gate with an entry-point assertion**

Whatever else changes, add to the parametrized test an assertion that entry-point resolution now succeeds for every fixture, so the Task 1 defect can never silently regress:

```python
    entry = resolve_entry_point(repo_graph)
    assert entry is not None, (
        f"{fixture_name}: entry-point resolution returned None, which silently "
        "disables the whole reachability analysis (see the 2026-07-27 cutover plan)"
    )
```

- [ ] **Step 4: Run the full graph suite**

Run: `uv run pytest tests/unit/pipeline/graph/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/pipeline/graph/test_fixtures_regression.py
git commit -m "test: assert entry-point resolution succeeds for every fixture"
```

---

### Task 3: The enrichment layer

**Files:**
- Create: `superrobot/pipeline/graph/enrich.py`
- Test: `tests/unit/pipeline/graph/test_enrich.py`

This is the single, well-bounded seam between the graph engine and the rest of the product: one function that takes the `ScanResult` scanner already produced and returns an improved one. Keeping it in one file means cutover (Task 4) touches exactly one call site.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/pipeline/graph/test_enrich.py`:

```python
"""Tests for graph-based enrichment of an existing ScanResult."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.enrich import enrich_scan_result
from superrobot.pipeline.scanner import scan


def test_enrichment_never_lowers_scanner_confidence(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from crewai import Agent\n\n"
        "def run_agent():\n"
        "    return Agent\n"
    )
    base = scan(tmp_path)
    enriched = enrich_scan_result(base, tmp_path)

    assert enriched.confidence >= base.confidence


def test_enrichment_preserves_fields_the_graph_does_not_own(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("crewai\n")
    (tmp_path / "main.py").write_text(
        "import os\n"
        "from crewai import Agent\n\n"
        "def run_agent():\n"
        "    return os.getenv('OPENAI_API_KEY'), Agent\n"
    )
    base = scan(tmp_path)
    enriched = enrich_scan_result(base, tmp_path)

    assert enriched.dependencies == base.dependencies
    assert enriched.env_vars == base.env_vars
    assert enriched.tools == base.tools
    assert enriched.llm_clients == base.llm_clients
    assert enriched.risk_flags == base.risk_flags
    assert enriched.python_file_count == base.python_file_count
    assert enriched.detected_framework == base.detected_framework


def test_enrichment_promotes_the_graph_resolved_entry_point_to_first(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text(
        "def process():\n"
        "    return 1\n\n"
        "def run_agent():\n"
        "    return process()\n"
    )
    base = scan(tmp_path)
    enriched = enrich_scan_result(base, tmp_path)

    assert enriched.primary_entry is not None
    assert enriched.primary_entry.function == "run_agent"


def test_enrichment_is_a_no_op_when_the_repo_cannot_be_graphed(tmp_path: Path) -> None:
    """A syntactically broken repo must degrade to the scanner's own result
    rather than failing the scan outright -- enrichment is strictly additive.
    """
    (tmp_path / "main.py").write_text("def broken(:\n    pass\n")
    base = scan(tmp_path)
    enriched = enrich_scan_result(base, tmp_path)

    assert enriched.detected_framework == base.detected_framework
    assert enriched.confidence == base.confidence
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/pipeline/graph/test_enrich.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superrobot.pipeline.graph.enrich'`

- [ ] **Step 3: Implement the enrichment layer**

Create `superrobot/pipeline/graph/enrich.py`:

```python
"""Enrich a scanner-produced ScanResult using the whole-repo call graph.

Deliberately an *enrichment* layer, not a replacement for
`superrobot.pipeline.scanner`. ScanResult has 15 fields; a call graph is
genuinely better at three of them (which entry point is real, how much to
trust the framework detection, and whether an import is actually reachable
at runtime). The other twelve -- dependencies parsed from requirements.txt,
env vars matched by regex, tools found by decorator, LLM clients found by
constructor name, secret-pattern risk flags, file counts -- gain nothing
from reachability analysis, so they are passed through untouched.

Two invariants make this safe to run unconditionally:

1. Conservative: enrichment may raise confidence (when the graph *proves* a
   framework is reachable from a real entry point) and may add findings, but
   never lowers a score below what the scanner reported. Cutover therefore
   cannot regress existing behavior.
2. Total: any failure to build or query the graph degrades to returning the
   scanner's own result unchanged, so a repo the graph can't handle still
   scans successfully.
"""

from __future__ import annotations

import logging
from pathlib import Path

from superrobot.models.scan_result import EntryPoint, ScanResult
from superrobot.pipeline.graph.builder import build_repo_graph, strip_collision_suffix
from superrobot.pipeline.graph.entry_points import resolve_entry_point
from superrobot.pipeline.graph.framework_detect import detect_framework

logger = logging.getLogger(__name__)


def enrich_scan_result(base: ScanResult, repo_path: str | Path) -> ScanResult:
    """Return `base` improved with graph-derived signal, or `base` unchanged
    if the repo cannot be graphed. Never raises.
    """
    try:
        repo_graph = build_repo_graph(Path(repo_path))
        entry_point = resolve_entry_point(repo_graph)
        detection = detect_framework(repo_graph, entry_point)
    except Exception as exc:  # pragma: no cover - defensive, see module docstring
        logger.debug("graph enrichment skipped for %s: %s", repo_path, exc)
        return base

    enriched = base.model_copy(deep=True)

    # (1) Confidence: only ever raised, and only when the graph independently
    # agrees with the scanner about which framework this is. A disagreement
    # means the graph is looking at something the scanner didn't conclude, so
    # it must not be used to inflate certainty.
    if detection.framework == base.detected_framework:
        enriched.confidence = max(base.confidence, detection.confidence)

    # (2) Entry points: promote the graph-resolved entry point to first, since
    # it was traced through real call edges rather than ranked by name alone.
    # Reorders only -- never drops a candidate the scanner found.
    if entry_point is not None:
        enriched.entry_points = _promote_entry_point(base.entry_points, entry_point)

    return enriched


def _promote_entry_point(
    entry_points: list[EntryPoint], entry_point: str
) -> list[EntryPoint]:
    """Move the scanner-discovered EntryPoint matching `entry_point` to the
    front of the list, leaving every other candidate in place.
    """
    local_name = strip_collision_suffix(entry_point).rsplit(".", 1)[-1]
    match = next((ep for ep in entry_points if ep.function == local_name), None)
    if match is None:
        return list(entry_points)
    return [match] + [ep for ep in entry_points if ep is not match]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/pipeline/graph/test_enrich.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add superrobot/pipeline/graph/enrich.py tests/unit/pipeline/graph/test_enrich.py
git commit -m "feat: add conservative graph-based ScanResult enrichment layer"
```

---

### Task 4: Cut the scan path over

**Files:**
- Modify: `superrobot/engine/pipeline.py`
- Test: `tests/unit/engine/test_pipeline_enrichment.py` (create; confirm the real directory name for engine tests first with `ls tests/unit/`)

- [ ] **Step 1: Write the failing test**

Create the test (adjust the path to match the repo's real engine-test location):

```python
"""The engine's scan stage must return graph-enriched results."""

from __future__ import annotations

from pathlib import Path

from superrobot.engine.pipeline import TransformEngine


def test_run_scan_returns_graph_enriched_result(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def process():\n"
        "    return 1\n\n"
        "def run_agent():\n"
        "    return process()\n"
    )

    result = TransformEngine().run_scan(str(tmp_path))

    # The graph traces run_agent as the real entry point; without enrichment
    # the scanner's name-ranking alone decides the order.
    assert result.primary_entry is not None
    assert result.primary_entry.function == "run_agent"


def test_run_scan_still_succeeds_on_a_repo_the_graph_cannot_parse(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text("def broken(:\n    pass\n")

    result = TransformEngine().run_scan(str(tmp_path))

    assert result is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/engine/test_pipeline_enrichment.py -v`
Expected: the first test FAILS (scan is not yet enriched).

- [ ] **Step 3: Wire enrichment into `run_scan`**

In `superrobot/engine/pipeline.py`, change `run_scan` from a bare passthrough to an enriched one:

```python
    def run_scan(self, repo_path: str) -> ScanResult:
        """Stage 1 — static scan, enriched with whole-repo graph analysis.

        Enrichment is conservative and total (see graph/enrich.py): it can
        only improve confidence and entry-point ordering, and degrades to the
        raw scanner result for any repo the graph can't handle.
        """
        self._emit("scan", repo_path)
        return enrich_scan_result(scan(repo_path), repo_path)
```

Add `from superrobot.pipeline.graph.enrich import enrich_scan_result` to the module's imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/engine/ -v`
Expected: all pass, including both new tests.

- [ ] **Step 5: Verify no regression across the whole suite**

Run: `uv run pytest -q`
Expected: everything passes except the one known pre-existing environment-caused failure, `tests/unit/test_cli.py::test_memory_ensure_blocked_without_auth`. Any *other* failure is a real regression from this cutover — fix it before continuing.

- [ ] **Step 6: Commit**

```bash
git add superrobot/engine/pipeline.py tests/unit/engine/test_pipeline_enrichment.py
git commit -m "feat: cut the scan stage over to graph-enriched results"
```

---

### Task 5: Surface unreachable-framework findings in validate

**Files:**
- Modify: `superrobot/pipeline/gap_analysis.py`
- Test: `tests/unit/pipeline/test_gap_analysis_unreachable.py` (confirm the real existing gap-analysis test path first)

This delivers the genuinely-new user-visible capability: telling someone a framework import in their repo never actually executes. It only became reachable (literally) once Task 1 landed.

- [ ] **Step 1: Write the failing test**

```python
"""run_gap_analysis should surface graph-detected unreachable imports."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.gap_analysis import run_gap_analysis


def test_reports_unreachable_framework_import_from_source_repo(
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "pkg"
    (package_dir / "agent" / "agent").mkdir(parents=True)
    (package_dir / "agent" / "agent" / "custom.py").write_text("# generated\n")

    source_repo = tmp_path / "src"
    source_repo.mkdir()
    (source_repo / "dead_code.py").write_text(
        "from crewai import Agent\n\n"
        "def unused():\n"
        "    return Agent\n"
    )
    (source_repo / "main.py").write_text(
        "from langgraph.graph import StateGraph\n\n"
        "def run_agent():\n"
        "    return StateGraph\n"
    )

    report = run_gap_analysis(package_dir, source_repo=source_repo)

    unreachable = [
        f for f in report.findings if f.rule == "unreachable-framework-import"
    ]
    assert unreachable, "expected an unreachable-framework-import finding"
    assert all(f.severity == "warning" for f in unreachable)
    assert any("crewai" in f.message for f in unreachable)


def test_no_unreachable_findings_when_no_source_repo_is_given(tmp_path: Path) -> None:
    package_dir = tmp_path / "pkg"
    (package_dir / "agent" / "agent").mkdir(parents=True)
    (package_dir / "agent" / "agent" / "custom.py").write_text("# generated\n")

    report = run_gap_analysis(package_dir)

    assert not [
        f for f in report.findings if f.rule == "unreachable-framework-import"
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/pipeline/test_gap_analysis_unreachable.py -v`
Expected: the first test FAILS (no such finding is produced today).

- [ ] **Step 3: Wire the graph check into `run_gap_analysis`**

In `superrobot/pipeline/gap_analysis.py`, inside `run_gap_analysis`, after the existing findings are collected and only when `source_repo is not None` (the check analyzes the *original* repo, not the generated package), append the graph-derived findings. Wrap in try/except so a graph failure can never break validation:

```python
    if source_repo is not None:
        try:
            from superrobot.pipeline.graph.builder import build_repo_graph
            from superrobot.pipeline.graph.entry_points import resolve_entry_point
            from superrobot.pipeline.graph.gap_analysis import (
                check_unreachable_frameworks,
            )

            repo_graph = build_repo_graph(Path(source_repo))
            findings.extend(
                check_unreachable_frameworks(
                    repo_graph, resolve_entry_point(repo_graph)
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("graph gap-analysis checks skipped: %s", exc)
```

Add a module-level `logger = logging.getLogger(__name__)` and `import logging` if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/pipeline/test_gap_analysis_unreachable.py -v`
Expected: 2 passed

- [ ] **Step 5: Confirm existing gap-analysis behavior is unchanged**

Run: `uv run pytest tests/unit/pipeline/ -v`
Expected: all pass — the new check is additive and must not alter any existing finding.

- [ ] **Step 6: Commit**

```bash
git add superrobot/pipeline/gap_analysis.py tests/unit/pipeline/test_gap_analysis_unreachable.py
git commit -m "feat: surface unreachable-framework-import findings during validate"
```

---

### Task 6: End-to-end verification on a real repo

**Files:** none (verification only)

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q`
Expected: all pass except the known `test_memory_ensure_blocked_without_auth` environment failure.

- [ ] **Step 2: Lint, format, types**

Run each; all must be clean (CI runs all three — a past PR broke CI by skipping the format check):
```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy superrobot/pipeline/graph/
```

- [ ] **Step 3: Prove the cutover end-to-end through the real CLI**

Run: `uv run superrobot scan tests/fixtures/langgraph_research_agent --json`
Expected: valid JSON on stdout whose `detected_framework` is `langgraph` and whose first entry point is `run_agent`. This exercises the real user-facing path (`cli.py` → `TransformEngine.run_scan` → enrichment), confirming the engine is no longer dead code.

- [ ] **Step 4: Prove the new validate finding end-to-end**

Construct a scratch repo with a reachable framework plus a dead, unreachable framework import, run `uv run superrobot validate` against a generated package with `--source` pointing at it, and confirm an `unreachable-framework-import` warning appears in the output. Record the exact commands and output in the commit message — this is the user-visible payoff of the whole effort.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: address issues found in cutover verification"
```

---

## Explicitly not in this plan

- **Deleting `scanner.py`/`ast_migrate.py`/`gap_analysis.py`.** Reversing the earlier plan's stated intent: they are the base layer this design enriches, not legacy to be removed.
- **Migrating the remaining `ast_migrate.py` transformers to `libcst`.** `graph/migrate.py`'s `rewrite_imports_libcst` is still narrower than `rewrite_imports_ast` (no `import x.y`, no prefix matching) and remains unwired; closing that gap is its own plan, and its current limitations are now documented in its docstring.
- **Reimplementing the 12 `ScanResult` fields the graph doesn't improve.** See Design decisions.
- **Any Phase 2 companion-UI work.** Separate plan, already shipped.
