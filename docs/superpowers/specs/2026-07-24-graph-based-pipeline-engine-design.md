# Graph-Based Pipeline Engine + DR-Styled UI Companion — Design

**Goal:** Replace SuperRobot's per-stage, heuristic AST scanning (scan/transform/validate each independently re-parsing the repo) with one shared, graph-based analysis engine that all three stages query — improving entry-point resolution, framework-detection confidence, and transform safety for Python agents. Follow it with an optional DataRobot-styled visual companion for the pipeline UI.

**Scope:** Python agents only (no multi-language support in this phase — confirmed with user). Two independently-shippable phases:
- **Phase 1 (primary, larger):** Graph-based engine for scan/transform/validate.
- **Phase 2 (follow-on):** DR-styled web UI companion.

This document specs both since they're one coherent vision, but the implementation plan should sequence them as two separate plans, Phase 1 first.

---

## Phase 1: Graph-Based Pipeline Engine

### Problem with the current architecture

- `superrobot/pipeline/scanner.py` detects frameworks via per-file `ast.Import`/`ast.ImportFrom` walks matched against hardcoded dicts (`FRAMEWORK_IMPORTS`, `FRAMEWORK_SYMBOLS`), and finds entry points by scoring candidate function names + filename bonuses (e.g. `run_agent` gets priority, `main.py` gets +20). Neither traces whether a detected import is actually *reachable* from where the program starts.
- `superrobot/pipeline/ast_migrate.py` does its own separate `ast.NodeTransformer`-based rewrite pass, using `ast.unparse()` to regenerate source — this can reformat/lose comments since it doesn't preserve the original concrete syntax.
- `superrobot/pipeline/gap_analysis.py` does its own separate validation file-scan.
- Net effect: three independent, ad hoc passes over the same repo, each capable of drawing different conclusions about the same code, and no stage can answer "is this import/entry-point actually used at runtime" — only "is it present somewhere."

### Architecture

A new `superrobot/pipeline/graph/` package builds one `RepoGraph` during `scan`, persisted as `graph.json` inside the `.superrobot/` output bundle. `transform` and `validate` load and query this same graph rather than re-parsing the repo.

**`graph/builder.py`**
- Walks all `.py` files in the target repo.
- Uses `jedi.Project`/`jedi.Script` for real cross-file symbol resolution (import targets, call resolution) — not string/regex matching.
- Builds a `networkx.DiGraph` with node types `module`, `function`, `class`, and edge types `imports`, `calls`, `defines`, `inherits`.
- Serializes via `networkx.node_link_data()` to `graph.json`.
- This is a one-shot build per `scan` invocation — not a live-watched incremental index. SuperRobot scans a repo once per pipeline run; a persistent watched index would solve an incremental-reindexing problem SuperRobot doesn't have. Caching to `graph.json` for reuse within the same run's later stages is the right-sized version of this.

**`graph/entry_points.py`**
Resolves the real entry point in priority order:
1. `pyproject.toml` `[project.scripts]` / `[tool.poetry.scripts]` console-script declaration, if present (most authoritative).
2. What is actually invoked from an `if __name__ == "__main__":` block, traced through the graph.
3. Today's existing name/filename heuristic scoring (`run_agent`, `main.py` bonus, etc.), used only as a fallback when neither of the above resolves anything — fully dynamic dispatch (constructed via `getattr`, plugin loaders, etc.) is not solvable by static analysis of any kind, graph-based or not; this is an explicit, acceptable limitation, not a bug to chase.

**`graph/framework_detect.py`**
- Keeps the existing domain-knowledge tables (`FRAMEWORK_IMPORTS`, `FRAMEWORK_SYMBOLS`, `DEPENDENCY_FRAMEWORKS`) verbatim — no static-analysis tool replaces knowing that `AssistantAgent` means autogen; this is irreducible domain knowledge, not something the graph rewrite removes.
- Changes the *confidence scoring*: a framework signal reachable from the resolved entry point (per `entry_points.py`) scores at today's high-confidence tier regardless of how many modules deep it's wrapped; a signal present in the repo but NOT reachable from the entry point is now reported as a separate, lower-priority finding (e.g. "unreachable framework import found: crewai — confirm this isn't leftover from an abandoned migration") rather than either being silently ignored or falsely inflating confidence in the wrong framework.

**`graph/queries.py`**
Shared, reusable query helpers used by scan, transform, and validate: `reachable_from(entry_point_node)`, `imports_of(module_node)`, `callers_of(function_node)`, `env_var_reads()` (still per-node regex/AST-based extraction, but attached to graph nodes instead of scanned blind across the whole repo).

### Transform: `libcst`-based codemods

Replace `ast_migrate.py`'s `ast.NodeTransformer` subclasses (`_ImportRewriter`, `_EnvRewriter`) with `libcst.CSTTransformer` equivalents:
- `libcst` (used in production at Meta for large-scale automated codemods) preserves the original concrete syntax tree exactly — comments, whitespace, formatting survive a rewrite. Today's `ast.unparse()`-based approach regenerates source from the abstract tree and can alter formatting.
- The transform pass uses `graph/queries.py`'s `reachable_from()` to scope which imports/env-reads actually need rewriting, instead of blindly walking every file in the repo.
- Behavior (import flattening, secret-default stripping, LLM client shimming) stays functionally identical to today — this is a safety/fidelity upgrade to *how* the rewrite happens, not a change to *what* it rewrites.

### Validate: graph-query-based Gap Analysis

- Existing rules (`flat-imports`, `endpoint-usage`, `runtime-param`, `pyproject-removal`) re-expressed as queries against the shared graph instead of independent file scans.
- New check enabled by the graph, not previously possible: flagging *unreachable* framework/import signals distinctly from reachable ones (surfaces as a `validate` warning, not blocking).

### Rollout strategy

Because this touches the core of three pipeline stages, it ships as a **parallel implementation path** first:
- New code lives under `superrobot/pipeline/graph/` and a new `graph_scanner.py`/`graph_migrate.py`/`graph_gap_analysis.py`, without deleting the existing `scanner.py`/`ast_migrate.py`/`gap_analysis.py`.
- A test suite captures the current scanner's output (detected framework + confidence, resolved entry point) for all 9 existing fixtures under `tests/fixtures/` (langchain, langgraph, crewai, llamaindex, autogen, semantic_kernel, haystack, smolagents, raw_async) as a baseline snapshot, then asserts the graph-based path produces the same detected framework and entry point for all 9, with confidence equal or higher. Any fixture where the graph-based path disagrees on the detected framework or entry point is a hard blocker on cutover — investigate and fix before proceeding, not a tolerated regression.
- Only after all 9 fixtures pass that gate does the CLI's default `scan`/`transform`/`validate` commands switch to calling the graph-based implementations; the old modules are removed in a follow-up cleanup task once the cutover is confirmed stable, not deleted in this same plan.

### New dependencies

`jedi`, `libcst`, `networkx` (all pure-Python, no external services or compiled toolchains beyond what's already required).

### Testing

- `tests/unit/pipeline/graph/test_builder.py` — graph construction against small synthetic repos (module/function/class nodes, import/call/defines/inherits edges).
- `tests/unit/pipeline/graph/test_entry_points.py` — priority-order resolution (console-script > `__main__` trace > heuristic fallback).
- `tests/unit/pipeline/graph/test_framework_detect.py` — reachable vs. unreachable confidence scoring.
- `tests/unit/pipeline/graph/test_fixtures_regression.py` — full scan against all 9 fixtures, asserting parity or improvement vs. today's scanner output (captured as a baseline snapshot).
- `tests/unit/pipeline/test_libcst_migrate.py` — transform output correctness + format-preservation assertions (comments/whitespace survive).
- `tests/unit/pipeline/test_graph_gap_analysis.py` — validate rules produce equivalent results to today's `gap_analysis.py`, plus the new unreachable-import check.

---

## Phase 2: DataRobot-Styled UI Companion (follow-on)

### Context

DataRobot publishes a real, actively-maintained React component library, `@datarobot/design-system` (npm), which is also what `af-component-react` (DataRobot's App Framework React scaffolding component) wires up via the `dr-ui` registry. This gives SuperRobot a concrete, real UI kit to build against rather than an abstract "make it look nice" goal.

### Architecture

- New `shell/companion/` package: a small React + Vite app built on `@datarobot/design-system`.
- Renders the same 5-stage pipeline (scan/transform/validate/deploy/receipts) that the terminal rail-widget already tracks, using DataRobot's real stepper/status/badge components instead of ASCII boxes.
- The shell (Node/Pi extension) spins up a small local HTTP+WebSocket server when a pipeline run starts, serving the companion app and pushing the same pipeline-state-reducer updates (already built earlier this session for the rail-widget) over a local WebSocket.
- Opened via a local URL in the user's browser, following the same pattern as Pi's own existing visual-companion tooling — not a new interaction paradigm, a second renderer for state that already exists.
- Strictly additive and opt-in: the terminal rail-widget remains the default, headless/CI-safe UI. The companion never becomes a requirement for any pipeline stage to function.

### Testing

- React Testing Library component tests for the stepper/status rendering given synthetic pipeline states.
- A smoke test asserting the local server boots, serves the app, and the WebSocket delivers a state update end-to-end.

### New dependencies

`@datarobot/design-system`, `react`, `react-dom`, `vite` (Node/shell side only — no Python dependency).

---

## Out of scope (this spec)

- Multi-language agent support (explicitly deferred — user confirmed Python is the current priority).
- CodeQL-style dataflow/taint analysis for validate (considered and rejected for now: too slow for an interactive `scan` step; worth revisiting for a future, deeper security-focused pass).
- Deleting the existing `scanner.py`/`ast_migrate.py`/`gap_analysis.py` modules (happens in a follow-up cleanup once the graph-based path is confirmed stable in production use, not in this plan).
- Any change to the DR-side deploy targets (Workload API / Agent App) — this spec only touches the local analysis/transform/validate pipeline and its UI.
