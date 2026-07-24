# Graph-Based Pipeline Engine (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SuperRobot's per-stage AST heuristics with one shared `RepoGraph` (built once during scan via `jedi` + `networkx`) that `scan`, `transform`, and `validate` all query, improving entry-point resolution and framework-detection confidence for Python agents.

**Architecture:** A new `superrobot/pipeline/graph/` package builds a `networkx.DiGraph` of modules/functions/classes and their import/call/defines relationships, using `ast` for structure and `jedi.Script.infer()` for real cross-file call resolution (verified: `.infer()` resolves through to the true definition; `.goto()` only follows one hop to the import — do not use `.goto()` for this). The graph is persisted as `graph.json` so later stages reuse it. This ships as a parallel path alongside the existing `scanner.py`/`ast_migrate.py`/`gap_analysis.py`, validated against all 9 test fixtures before any cutover.

**Tech Stack:** `jedi` (cross-file symbol resolution), `networkx` (graph data structure + reachability queries), `libcst` (format-preserving code transforms), Python 3.11+, `pytest`.

**Spec:** `docs/superpowers/specs/2026-07-24-graph-based-pipeline-engine-design.md`

---

### Task 1: Add dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the three new dependencies**

In `pyproject.toml`, under `[project]` `dependencies = [...]`, add three entries so the block reads:

```toml
dependencies = [
    "typer>=0.12.0",
    "rich>=13.0.0",
    "pydantic>=2.0",
    "httpx>=0.27.0",
    "pyyaml>=6.0",
    "jinja2>=3.1.0",
    "openai>=1.0.0",
    "jedi>=0.20.0",
    "libcst>=1.8.0",
    "networkx>=3.6.0",
]
```

- [ ] **Step 2: Sync the environment**

Run: `uv sync`
Expected: resolves and installs `jedi`, `libcst`, `networkx`, `parso` (jedi's dependency) with no errors.

- [ ] **Step 3: Verify imports work**

Run: `uv run python3 -c "import jedi, libcst, networkx; print(jedi.__version__, libcst.__version__, networkx.__version__)"`
Expected: prints three version strings, no `ModuleNotFoundError`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build: add jedi, libcst, networkx dependencies for graph-based pipeline engine"
```

(`uv.lock` is gitignored in this repo — same convention as `shell/package-lock.json` — do not force-add it.)

---

### Task 2: `RepoGraph` data structure + save/load

**Files:**
- Create: `superrobot/pipeline/graph/__init__.py`
- Create: `superrobot/pipeline/graph/builder.py`
- Test: `tests/unit/pipeline/graph/test_builder.py`

- [ ] **Step 1: Create the package directory and test file with a failing test**

Create `tests/unit/pipeline/graph/__init__.py` (empty file).

Create `tests/unit/pipeline/graph/test_builder.py`:

```python
"""Tests for RepoGraph construction, save, and load."""

from __future__ import annotations

import json
from pathlib import Path

from superrobot.pipeline.graph.builder import RepoGraph, module_dotted_name


def test_module_dotted_name_for_top_level_file(tmp_path: Path) -> None:
    repo_root = tmp_path
    py_file = repo_root / "main.py"
    py_file.write_text("")
    assert module_dotted_name(py_file, repo_root) == "main"


def test_module_dotted_name_for_nested_file(tmp_path: Path) -> None:
    repo_root = tmp_path
    py_file = repo_root / "pkg" / "tools.py"
    py_file.parent.mkdir()
    py_file.write_text("")
    assert module_dotted_name(py_file, repo_root) == "pkg.tools"


def test_module_dotted_name_strips_init(tmp_path: Path) -> None:
    repo_root = tmp_path
    py_file = repo_root / "pkg" / "__init__.py"
    py_file.parent.mkdir()
    py_file.write_text("")
    assert module_dotted_name(py_file, repo_root) == "pkg"


def test_save_and_load_round_trips_graph(tmp_path: Path) -> None:
    import networkx as nx

    graph = nx.DiGraph()
    graph.add_node("main", kind="module", path="/tmp/main.py")
    graph.add_node("main.run", kind="function", path="/tmp/main.py", line=3)
    graph.add_edge("main", "main.run", kind="defines")

    repo_graph = RepoGraph(graph=graph, repo_root=tmp_path)
    out_path = tmp_path / "graph.json"
    repo_graph.save(out_path)

    assert out_path.is_file()
    data = json.loads(out_path.read_text())
    assert data["directed"] is True

    loaded = RepoGraph.load(out_path, repo_root=tmp_path)
    assert set(loaded.graph.nodes) == {"main", "main.run"}
    assert loaded.graph.nodes["main.run"]["kind"] == "function"
    assert loaded.graph.has_edge("main", "main.run")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/pipeline/graph/test_builder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superrobot.pipeline.graph'`

- [ ] **Step 3: Implement `RepoGraph` and `module_dotted_name`**

Create `superrobot/pipeline/graph/__init__.py` (empty file).

Create `superrobot/pipeline/graph/builder.py`:

```python
"""Build a whole-repo code graph (modules, functions, classes, and their
import/call/defines relationships) using ast for structure and jedi for
real cross-file call resolution.

Verified behavior (see spec): jedi's Script.infer(line, column) resolves
a call site through to its true cross-file definition. Script.goto()
does NOT do this reliably -- it can stop at the import statement rather
than the original definition. Always use infer(), not goto(), when
resolving call targets for the graph's "calls" edges.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

_EXCLUDED_DIR_NAMES = {"__pycache__", "venv", ".venv", "node_modules", ".git"}


def module_dotted_name(py_file: Path, repo_root: Path) -> str:
    """Compute the dotted module name for a .py file relative to repo_root.

    tests/fixtures/pkg/tools.py under repo_root tests/fixtures -> "pkg.tools"
    pkg/__init__.py -> "pkg" (the __init__ segment is dropped)
    """
    rel = py_file.relative_to(repo_root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else py_file.stem


def iter_python_files(repo_root: Path) -> list[Path]:
    """List all .py files under repo_root, skipping venvs/caches."""
    return [
        p
        for p in repo_root.rglob("*.py")
        if not any(part in _EXCLUDED_DIR_NAMES or part.startswith(".") for part in p.parts)
    ]


@dataclass
class RepoGraph:
    """A whole-repo code graph plus the root it was built from."""

    graph: nx.DiGraph
    repo_root: Path

    def save(self, path: Path) -> None:
        """Serialize the graph to JSON at path."""
        data = nx.node_link_data(self.graph, edges="edges")
        path.write_text(json.dumps(data, default=str))

    @classmethod
    def load(cls, path: Path, repo_root: Path) -> "RepoGraph":
        """Load a previously-saved graph from path."""
        data = json.loads(path.read_text())
        graph = nx.node_link_graph(data, edges="edges")
        return cls(graph=graph, repo_root=repo_root)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/pipeline/graph/test_builder.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add superrobot/pipeline/graph/__init__.py superrobot/pipeline/graph/builder.py tests/unit/pipeline/graph/__init__.py tests/unit/pipeline/graph/test_builder.py
git commit -m "feat: add RepoGraph data structure with save/load"
```

---

### Task 3: Build the graph — structure pass (modules, functions, classes, imports)

**Files:**
- Modify: `superrobot/pipeline/graph/builder.py`
- Test: `tests/unit/pipeline/graph/test_builder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/pipeline/graph/test_builder.py`:

```python
def test_build_repo_graph_structure_pass(tmp_path: Path) -> None:
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "tools.py").write_text("def search(query: str) -> str:\n    return query\n")
    (tmp_path / "main.py").write_text(
        "from pkg.tools import search\n\n"
        "def run_agent():\n"
        "    return search('hello')\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    assert graph.nodes["main"]["kind"] == "module"
    assert graph.nodes["pkg.tools"]["kind"] == "module"
    assert graph.nodes["main.run_agent"]["kind"] == "function"
    assert graph.nodes["main.run_agent"]["line"] == 3
    assert graph.nodes["pkg.tools.search"]["kind"] == "function"

    assert graph.has_edge("main", "main.run_agent")
    assert graph.get_edge_data("main", "main.run_agent")["kind"] == "defines"
    assert graph.has_edge("pkg.tools", "pkg.tools.search")

    assert graph.has_edge("main", "pkg.tools")
    assert graph.get_edge_data("main", "pkg.tools")["kind"] == "imports"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/pipeline/graph/test_builder.py::test_build_repo_graph_structure_pass -v`
Expected: FAIL with `ImportError: cannot import name 'build_repo_graph'`

- [ ] **Step 3: Implement the structure pass**

Add to `superrobot/pipeline/graph/builder.py` (append after the `RepoGraph` class):

```python
import ast


def build_repo_graph(repo_root: Path) -> RepoGraph:
    """Build a RepoGraph for the Python repo at repo_root.

    Two passes: first ast-based structure (modules, function/class
    definitions, defines/imports edges), then a separate call-resolution
    pass (Task 4) adds "calls" edges via jedi.
    """
    repo_root = Path(repo_root)
    graph = nx.DiGraph()

    for py_file in iter_python_files(repo_root):
        try:
            source = py_file.read_text()
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue

        mod_name = module_dotted_name(py_file, repo_root)
        graph.add_node(mod_name, kind="module", path=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual_id = f"{mod_name}.{node.name}"
                node_kind = "class" if isinstance(node, ast.ClassDef) else "function"
                graph.add_node(qual_id, kind=node_kind, path=str(py_file), line=node.lineno)
                graph.add_edge(mod_name, qual_id, kind="defines")

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                graph.add_edge(mod_name, node.module, kind="imports")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    graph.add_edge(mod_name, alias.name, kind="imports")

    return graph_with_repo_root(graph, repo_root)


def graph_with_repo_root(graph: nx.DiGraph, repo_root: Path) -> RepoGraph:
    return RepoGraph(graph=graph, repo_root=repo_root)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/pipeline/graph/test_builder.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add superrobot/pipeline/graph/builder.py tests/unit/pipeline/graph/test_builder.py
git commit -m "feat: build module/function/class/import structure into RepoGraph"
```

---

### Task 4: Add cross-file call resolution via jedi

**Files:**
- Modify: `superrobot/pipeline/graph/builder.py`
- Test: `tests/unit/pipeline/graph/test_builder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/pipeline/graph/test_builder.py`:

```python
def test_build_repo_graph_resolves_cross_file_calls(tmp_path: Path) -> None:
    from superrobot.pipeline.graph.builder import build_repo_graph

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "tools.py").write_text("def search(query: str) -> str:\n    return query\n")
    (tmp_path / "main.py").write_text(
        "from pkg.tools import search\n\n"
        "def run_agent():\n"
        "    return search('hello')\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )

    repo_graph = build_repo_graph(tmp_path)
    graph = repo_graph.graph

    assert graph.has_edge("main.run_agent", "pkg.tools.search")
    assert graph.get_edge_data("main.run_agent", "pkg.tools.search")["kind"] == "calls"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/pipeline/graph/test_builder.py::test_build_repo_graph_resolves_cross_file_calls -v`
Expected: FAIL — no `calls` edge exists yet (`AssertionError`)

- [ ] **Step 3: Implement the call-resolution pass**

Replace the `build_repo_graph` function in `superrobot/pipeline/graph/builder.py` with this version that adds a second pass:

```python
import jedi


def build_repo_graph(repo_root: Path) -> RepoGraph:
    """Build a RepoGraph for the Python repo at repo_root.

    Pass 1: ast-based structure (modules, function/class definitions,
    defines/imports edges).
    Pass 2: cross-file call resolution via jedi.Script.infer() -- NOT
    .goto(), which only follows one hop to the import statement rather
    than the true definition (verified against real jedi 0.20 behavior).
    """
    repo_root = Path(repo_root)
    graph = nx.DiGraph()
    py_files = iter_python_files(repo_root)
    file_asts: dict[Path, ast.Module] = {}
    module_names: dict[Path, str] = {}

    for py_file in py_files:
        try:
            source = py_file.read_text()
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue

        file_asts[py_file] = tree
        mod_name = module_dotted_name(py_file, repo_root)
        module_names[py_file] = mod_name
        graph.add_node(mod_name, kind="module", path=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual_id = f"{mod_name}.{node.name}"
                node_kind = "class" if isinstance(node, ast.ClassDef) else "function"
                graph.add_node(qual_id, kind=node_kind, path=str(py_file), line=node.lineno)
                graph.add_edge(mod_name, qual_id, kind="defines")

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                graph.add_edge(mod_name, node.module, kind="imports")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    graph.add_edge(mod_name, alias.name, kind="imports")

    project = jedi.Project(path=str(repo_root))
    for py_file, tree in file_asts.items():
        mod_name = module_names[py_file]
        try:
            script = jedi.Script(path=str(py_file), project=project)
        except Exception:
            continue

        for func_node in ast.walk(tree):
            if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            caller_id = f"{mod_name}.{func_node.name}"

            for call in ast.walk(func_node):
                if not isinstance(call, ast.Call):
                    continue
                target = call.func
                if isinstance(target, ast.Name):
                    line, col = target.lineno, target.col_offset
                elif isinstance(target, ast.Attribute):
                    line = target.end_lineno
                    col = target.end_col_offset - len(target.attr)
                else:
                    continue

                try:
                    inferred = script.infer(line=line, column=col)
                except Exception:
                    continue

                for target_def in inferred:
                    if not target_def.module_path or not target_def.full_name:
                        continue
                    target_path = Path(target_def.module_path)
                    if not target_path.is_relative_to(repo_root):
                        continue
                    # jedi's full_name is already fully qualified through
                    # enclosing classes (verified: a method f.search() on
                    # class Foo infers full_name="pkg.tools.Foo.search"),
                    # which matches this graph's node-id scheme exactly
                    # (module_dotted_name + nested-qualified def name from
                    # Task 3's parent-tracking fix). Do NOT reconstruct
                    # from target_def.name alone -- that's only the bare
                    # method name and would silently fail to match nested
                    # node ids for class methods.
                    callee_id = target_def.full_name
                    if callee_id in graph:
                        graph.add_edge(caller_id, callee_id, kind="calls")

    return RepoGraph(graph=graph, repo_root=repo_root)
```

Delete the now-unused `graph_with_repo_root` helper function added in Task 3 (its logic is inlined above).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/pipeline/graph/test_builder.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add superrobot/pipeline/graph/builder.py tests/unit/pipeline/graph/test_builder.py
git commit -m "feat: resolve cross-file call edges via jedi.Script.infer()"
```

---

### Task 5: Entry-point resolution

**Files:**
- Create: `superrobot/pipeline/graph/entry_points.py`
- Test: `tests/unit/pipeline/graph/test_entry_points.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/pipeline/graph/test_entry_points.py`:

```python
"""Tests for graph-based entry-point resolution."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.entry_points import resolve_entry_point


def test_resolves_entry_point_from_main_guard(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def run_agent():\n    return 'ok'\n\nif __name__ == '__main__':\n    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)

    assert resolve_entry_point(repo_graph) == "main.run_agent"


def test_prefers_pyproject_console_script(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\nmyagent = "main:run_agent"\n')
    (tmp_path / "main.py").write_text(
        "def run_agent():\n"
        "    return 'ok'\n\n"
        "def other():\n"
        "    return 'nope'\n\n"
        "if __name__ == '__main__':\n"
        "    other()\n"
    )
    repo_graph = build_repo_graph(tmp_path)

    assert resolve_entry_point(repo_graph) == "main.run_agent"


def test_returns_none_when_unresolvable(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x = 1\n")
    repo_graph = build_repo_graph(tmp_path)

    assert resolve_entry_point(repo_graph) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/pipeline/graph/test_entry_points.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superrobot.pipeline.graph.entry_points'`

- [ ] **Step 3: Implement entry-point resolution**

Create `superrobot/pipeline/graph/entry_points.py`:

```python
"""Resolve the real entry point of a scanned repo.

Priority order:
1. pyproject.toml [project.scripts] console-script declaration, if present
   and it resolves to a node already in the graph (most authoritative).
2. What is actually invoked from an `if __name__ == "__main__":` guard,
   traced through the graph.
3. None -- callers should fall back to the existing name/filename
   heuristic scoring in superrobot.pipeline.scanner when this returns
   None. Fully dynamic dispatch (getattr, plugin loaders, etc.) cannot
   be resolved statically by any tool, graph-based or not.
"""

from __future__ import annotations

import ast
import tomllib

from superrobot.pipeline.graph.builder import RepoGraph, module_dotted_name


def resolve_entry_point(repo_graph: RepoGraph) -> str | None:
    """Return the graph node id of the real entry point, or None."""
    console_script_target = _resolve_console_script(repo_graph)
    if console_script_target is not None:
        return console_script_target

    return _resolve_main_guard_call(repo_graph)


def _resolve_console_script(repo_graph: RepoGraph) -> str | None:
    pyproject_path = repo_graph.repo_root / "pyproject.toml"
    if not pyproject_path.is_file():
        return None
    try:
        data = tomllib.loads(pyproject_path.read_text())
    except tomllib.TOMLDecodeError:
        return None

    scripts = data.get("project", {}).get("scripts", {})
    for target in scripts.values():
        module_part, _, func_part = target.partition(":")
        if not func_part:
            continue
        candidate = f"{module_part}.{func_part}"
        if candidate in repo_graph.graph:
            return candidate
    return None


def _resolve_main_guard_call(repo_graph: RepoGraph) -> str | None:
    for py_file in repo_graph.repo_root.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue

        mod_name = module_dotted_name(py_file, repo_graph.repo_root)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.If) and _is_main_guard(node)):
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                    candidate = f"{mod_name}.{call.func.id}"
                    if candidate in repo_graph.graph:
                        return candidate
    return None


def _is_main_guard(node: ast.If) -> bool:
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(
            isinstance(comparator, ast.Constant) and comparator.value == "__main__"
            for comparator in test.comparators
        )
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/pipeline/graph/test_entry_points.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add superrobot/pipeline/graph/entry_points.py tests/unit/pipeline/graph/test_entry_points.py
git commit -m "feat: resolve real entry points via pyproject scripts and __main__ trace"
```

---

### Task 6: Reachability-weighted framework detection

**Files:**
- Create: `superrobot/pipeline/graph/framework_detect.py`
- Test: `tests/unit/pipeline/graph/test_framework_detect.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/pipeline/graph/test_framework_detect.py`:

```python
"""Tests for reachability-weighted framework detection."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.entry_points import resolve_entry_point
from superrobot.pipeline.graph.framework_detect import detect_framework


def test_detects_reachable_framework_with_high_confidence(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from langgraph.graph import StateGraph\n\n"
        "def run_agent():\n"
        "    return StateGraph\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework == "langgraph"
    assert result.confidence >= 0.9
    assert result.unreachable_warnings == []


def test_flags_unreachable_framework_import_separately(tmp_path: Path) -> None:
    (tmp_path / "dead_code.py").write_text(
        "from crewai import Agent\n\ndef unused():\n    return Agent\n"
    )
    (tmp_path / "main.py").write_text(
        "from langgraph.graph import StateGraph\n\n"
        "def run_agent():\n"
        "    return StateGraph\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework == "langgraph"
    assert any("crewai" in warning for warning in result.unreachable_warnings)


def test_returns_unknown_with_low_confidence_when_no_framework_found(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x = 1\n")
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework == "unknown"
    assert result.confidence <= 0.3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/pipeline/graph/test_framework_detect.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superrobot.pipeline.graph.framework_detect'`

- [ ] **Step 3: Implement reachability-weighted detection**

Create `superrobot/pipeline/graph/framework_detect.py`:

```python
"""Framework detection weighted by reachability from the resolved entry
point. Reuses the exact FRAMEWORK_IMPORTS domain-knowledge table from
superrobot.pipeline.scanner -- no static-analysis tool replaces knowing
that a given import name means a given framework; the graph only changes
how confidently we act on that knowledge (is it actually used at runtime,
or just present somewhere in the repo).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from superrobot.pipeline.graph.builder import RepoGraph
from superrobot.pipeline.scanner import FRAMEWORK_IMPORTS


@dataclass
class FrameworkDetection:
    framework: str
    confidence: float
    unreachable_warnings: list[str] = field(default_factory=list)


def detect_framework(repo_graph: RepoGraph, entry_point: str | None) -> FrameworkDetection:
    """Detect the primary framework, weighting reachability from entry_point."""
    graph = repo_graph.graph

    reachable: set[str] = set()
    if entry_point is not None and entry_point in graph:
        # Functions/classes actually reachable from the entry point via real
        # `calls` edges.
        reachable_functions = nx.descendants(graph, entry_point) | {entry_point}
        reachable |= reachable_functions

        for func_node in reachable_functions:
            # Find the function's real containing module via the reverse
            # `defines` edge (module --defines--> function), never by
            # string-splitting the node id -- entry points can be nested
            # (e.g. "pkg.sub.run_agent" lives in module "pkg.sub", not "pkg".
            # An earlier draft of this function used entry_point.split(".")[0]
            # here, which broke for any nested entry point -- verified via a
            # regression test before landing this version).
            for module_node, _, edge_attrs in graph.in_edges(func_node, data=True):
                if edge_attrs.get("kind") != "defines":
                    continue
                reachable.add(module_node)
                # A framework import only "counts" as reachable if it's
                # actually imported by a module on the entry point's real
                # call path, so pull in that module's `imports` targets too.
                for _, imported, imports_attrs in graph.out_edges(module_node, data=True):
                    if imports_attrs.get("kind") == "imports":
                        reachable.add(imported)

    reachable_frameworks: dict[str, str] = {}  # framework -> matched module node
    unreachable_frameworks: dict[str, str] = {}
    unreachable_warnings: list[str] = []

    for node, attrs in graph.nodes(data=True):
        # Check both local modules and external imports (which may have no 'kind')
        kind = attrs.get("kind")
        if kind not in ("module", None):
            continue
        for prefix, framework in FRAMEWORK_IMPORTS.items():
            if node != prefix and not node.startswith(prefix + "."):
                continue
            is_reachable = (not reachable) or (node in reachable)
            if is_reachable:
                reachable_frameworks.setdefault(framework, node)
            else:
                unreachable_frameworks.setdefault(framework, node)
                unreachable_warnings.append(
                    f"unreachable framework import found: {framework} ({node}) -- "
                    "confirm this isn't leftover from an abandoned migration"
                )

    if reachable_frameworks:
        framework = next(iter(reachable_frameworks))
        return FrameworkDetection(
            framework=framework, confidence=0.95, unreachable_warnings=unreachable_warnings
        )

    if unreachable_frameworks:
        framework = next(iter(unreachable_frameworks))
        return FrameworkDetection(
            framework=framework, confidence=0.4, unreachable_warnings=unreachable_warnings
        )

    return FrameworkDetection(framework="unknown", confidence=0.2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/pipeline/graph/test_framework_detect.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add superrobot/pipeline/graph/framework_detect.py tests/unit/pipeline/graph/test_framework_detect.py
git commit -m "feat: reachability-weighted framework detection"
```

---

### Task 7: Shared graph query helpers

**Files:**
- Create: `superrobot/pipeline/graph/queries.py`
- Test: `tests/unit/pipeline/graph/test_queries.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/pipeline/graph/test_queries.py`:

```python
"""Tests for shared graph query helpers used by scan/transform/validate."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.queries import callers_of, imports_of, reachable_from


def _build(tmp_path: Path) -> "RepoGraph":  # noqa: F821 - forward ref in docstring only
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "tools.py").write_text("def search(query: str) -> str:\n    return query\n")
    (tmp_path / "main.py").write_text(
        "from pkg.tools import search\n\n"
        "def run_agent():\n"
        "    return search('hello')\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    from superrobot.pipeline.graph.builder import build_repo_graph as _build_repo_graph

    return _build_repo_graph(tmp_path)


def test_reachable_from_entry_point(tmp_path: Path) -> None:
    repo_graph = _build(tmp_path)
    result = reachable_from(repo_graph, "main.run_agent")
    assert "pkg.tools.search" in result


def test_imports_of_module(tmp_path: Path) -> None:
    repo_graph = _build(tmp_path)
    assert imports_of(repo_graph, "main") == ["pkg.tools"]


def test_callers_of_function(tmp_path: Path) -> None:
    repo_graph = _build(tmp_path)
    assert callers_of(repo_graph, "pkg.tools.search") == ["main.run_agent"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/pipeline/graph/test_queries.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superrobot.pipeline.graph.queries'`

- [ ] **Step 3: Implement the query helpers**

Create `superrobot/pipeline/graph/queries.py`:

```python
"""Shared, reusable graph queries used by scan, transform, and validate
so all three stages reason about the same repo structure instead of
each doing an independent ad hoc pass.
"""

from __future__ import annotations

import networkx as nx

from superrobot.pipeline.graph.builder import RepoGraph


def reachable_from(repo_graph: RepoGraph, node_id: str) -> set[str]:
    """Return all nodes reachable from node_id (excluding node_id itself)."""
    if node_id not in repo_graph.graph:
        return set()
    return nx.descendants(repo_graph.graph, node_id)


def imports_of(repo_graph: RepoGraph, module_node_id: str) -> list[str]:
    """Return the module names directly imported by module_node_id."""
    graph = repo_graph.graph
    if module_node_id not in graph:
        return []
    return [
        target
        for target in graph.successors(module_node_id)
        if graph.get_edge_data(module_node_id, target).get("kind") == "imports"
    ]


def callers_of(repo_graph: RepoGraph, function_node_id: str) -> list[str]:
    """Return the function/class nodes that call function_node_id."""
    graph = repo_graph.graph
    if function_node_id not in graph:
        return []
    return [
        source
        for source in graph.predecessors(function_node_id)
        if graph.get_edge_data(source, function_node_id).get("kind") == "calls"
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/pipeline/graph/test_queries.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add superrobot/pipeline/graph/queries.py tests/unit/pipeline/graph/test_queries.py
git commit -m "feat: shared graph query helpers (reachable_from, imports_of, callers_of)"
```

---

### Task 8: Fixture regression gate

**Files:**
- Create: `tests/unit/pipeline/graph/test_fixtures_regression.py`

- [ ] **Step 1: Write the parametrized regression test**

Create `tests/unit/pipeline/graph/test_fixtures_regression.py`:

```python
"""Regression gate: the graph-based path must detect the same framework
as today's scanner.py for every existing test fixture, with confidence
equal or higher. Per the spec, any disagreement here is a hard blocker
on cutover -- investigate and fix, do not relax this test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.entry_points import resolve_entry_point
from superrobot.pipeline.graph.framework_detect import detect_framework
from superrobot.pipeline.scanner import scan

FIXTURES_ROOT = Path(__file__).parent.parent.parent.parent / "fixtures"

FIXTURE_DIRS = [
    "langchain_agent",
    "langgraph_research_agent",
    "crewai_agent",
    "llamaindex_agent",
    "autogen_agent",
    "semantic_kernel_agent",
    "haystack_agent",
    "smolagents_agent",
    "raw_async_agent",
]


@pytest.mark.parametrize("fixture_name", FIXTURE_DIRS)
def test_graph_based_detection_matches_or_improves_on_scanner(fixture_name: str) -> None:
    fixture_path = FIXTURES_ROOT / fixture_name
    baseline = scan(fixture_path)

    repo_graph = build_repo_graph(fixture_path)
    entry = resolve_entry_point(repo_graph)
    result = detect_framework(repo_graph, entry)

    assert result.framework == baseline.detected_framework, (
        f"{fixture_name}: graph-based detected {result.framework!r}, "
        f"scanner.py detected {baseline.detected_framework!r}"
    )
    assert result.confidence >= baseline.confidence, (
        f"{fixture_name}: graph-based confidence {result.confidence} is lower than "
        f"scanner.py's {baseline.confidence}"
    )
```

- [ ] **Step 2: Run the regression suite**

Run: `uv run pytest tests/unit/pipeline/graph/test_fixtures_regression.py -v`
Expected: All 9 parametrized cases pass. If any fail, the failure message names the exact fixture and the mismatch (detected framework or confidence) — fix the graph builder/detector (Tasks 3, 4, or 6) for that case before proceeding. Do not weaken this test to make it pass.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/pipeline/graph/test_fixtures_regression.py
git commit -m "test: add 9-fixture regression gate for graph-based detection"
```

---

### Task 9: `libcst`-based import rewriter (format-preserving transform)

**Files:**
- Create: `superrobot/pipeline/graph/migrate.py`
- Test: `tests/unit/pipeline/graph/test_migrate.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/pipeline/graph/test_migrate.py`:

```python
"""Tests for the libcst-based import rewriter -- format-preserving
replacement for ast_migrate.py's _ImportRewriter, which uses
ast.unparse() and can alter formatting/comments.
"""

from __future__ import annotations

from superrobot.pipeline.graph.migrate import rewrite_imports_libcst


def test_rewrites_nested_import_to_flat_name() -> None:
    source = "from tools.search import search\n"
    result, count = rewrite_imports_libcst(source, {"tools.search": "search"})

    assert count == 1
    assert result == "from search import search\n"


def test_preserves_comments_and_formatting() -> None:
    source = (
        "# this is a real dependency, do not remove\n"
        "from tools.search import search\n\n"
        "def run():\n"
        "    return search()\n"
    )
    result, count = rewrite_imports_libcst(source, {"tools.search": "search"})

    assert count == 1
    assert "# this is a real dependency, do not remove" in result
    assert "from search import search" in result
    assert "def run():" in result


def test_leaves_unrelated_imports_unchanged() -> None:
    source = "import os\nfrom typing import Any\n"
    result, count = rewrite_imports_libcst(source, {"tools.search": "search"})

    assert count == 0
    assert result == source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/pipeline/graph/test_migrate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superrobot.pipeline.graph.migrate'`

- [ ] **Step 3: Implement the libcst-based rewriter**

Create `superrobot/pipeline/graph/migrate.py`:

```python
"""Format-preserving import rewriting via libcst, replacing
ast_migrate.py's ast.NodeTransformer + ast.unparse() approach.

libcst round-trips the concrete syntax tree exactly -- comments and
whitespace survive a rewrite, unlike ast.unparse() which regenerates
source from the abstract tree and can reformat it. Verified: a leading
comment above a rewritten import statement is preserved unchanged.
"""

from __future__ import annotations

import libcst as cst


class _ImportRewriter(cst.CSTTransformer):
    """Rewrite `from <nested.module> import X` to `from <flat> import X`
    when nested.module is a key in flat_names.
    """

    def __init__(self, flat_names: dict[str, str]) -> None:
        self.flat_names = flat_names
        self.rewrites = 0

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        if updated_node.module is None:
            return updated_node
        mod_name = cst.helpers.get_full_name_for_node(updated_node.module)
        if mod_name in self.flat_names:
            self.rewrites += 1
            new_module = cst.parse_expression(self.flat_names[mod_name])
            return updated_node.with_changes(module=new_module, relative=[])
        return updated_node


def rewrite_imports_libcst(content: str, flat_names: dict[str, str]) -> tuple[str, int]:
    """Rewrite nested imports of migrated modules to flat names.

    Returns (new_source, rewrite_count). Format/comments are preserved
    exactly except for the rewritten import lines themselves.
    """
    tree = cst.parse_module(content)
    transformer = _ImportRewriter(flat_names)
    new_tree = tree.visit(transformer)
    return new_tree.code, transformer.rewrites
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/pipeline/graph/test_migrate.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add superrobot/pipeline/graph/migrate.py tests/unit/pipeline/graph/test_migrate.py
git commit -m "feat: libcst-based format-preserving import rewriter"
```

**Note:** this task migrates the `_ImportRewriter` only, as the representative first case. `_EnvRewriter` and the other `ast_migrate.py` transformers move to `libcst` in the same pattern as a fast-follow — not included here to keep this task bite-sized; tracked in "Not in this plan" below.

---

### Task 10: Graph-based gap analysis check (unreachable framework imports)

**Files:**
- Create: `superrobot/pipeline/graph/gap_analysis.py`
- Test: `tests/unit/pipeline/graph/test_gap_analysis.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/pipeline/graph/test_gap_analysis.py`:

```python
"""Tests for the graph-native gap analysis check: flagging unreachable
framework imports as a distinct, non-blocking finding. This check was
not previously possible with gap_analysis.py's file-scan approach,
since it requires knowing what's reachable from the entry point.
"""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.entry_points import resolve_entry_point
from superrobot.pipeline.graph.gap_analysis import check_unreachable_frameworks


def test_flags_unreachable_framework_as_warning(tmp_path: Path) -> None:
    (tmp_path / "dead_code.py").write_text(
        "from crewai import Agent\n\ndef unused():\n    return Agent\n"
    )
    (tmp_path / "main.py").write_text(
        "from langgraph.graph import StateGraph\n\n"
        "def run_agent():\n"
        "    return StateGraph\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    findings = check_unreachable_frameworks(repo_graph, entry)

    assert len(findings) == 1
    assert findings[0].rule == "unreachable-framework-import"
    assert findings[0].severity == "warning"
    assert "crewai" in findings[0].message


def test_no_findings_when_everything_reachable(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from langgraph.graph import StateGraph\n\n"
        "def run_agent():\n"
        "    return StateGraph\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    findings = check_unreachable_frameworks(repo_graph, entry)

    assert findings == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/pipeline/graph/test_gap_analysis.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superrobot.pipeline.graph.gap_analysis'`

- [ ] **Step 3: Implement the check**

Create `superrobot/pipeline/graph/gap_analysis.py`:

```python
"""Graph-native gap analysis checks. Reuses GapFinding from the canonical
superrobot.models.gap_result module (superrobot.pipeline.gap_analysis
does not re-export it) so results compose with today's GapReport
unchanged -- this adds one new check, it does not replace the existing
file-scan-based rules (flat-imports, endpoint-usage, pyproject-removal,
runtime-param), which stay as they are for now.
"""

from __future__ import annotations

from superrobot.models.gap_result import GapFinding
from superrobot.pipeline.graph.builder import RepoGraph
from superrobot.pipeline.graph.framework_detect import detect_framework


def check_unreachable_frameworks(
    repo_graph: RepoGraph, entry_point: str | None
) -> list[GapFinding]:
    """Flag framework imports present in the repo but not reachable from
    the resolved entry point, as a non-blocking warning.
    """
    result = detect_framework(repo_graph, entry_point)
    return [
        GapFinding(rule="unreachable-framework-import", severity="warning", message=warning)
        for warning in result.unreachable_warnings
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/pipeline/graph/test_gap_analysis.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add superrobot/pipeline/graph/gap_analysis.py tests/unit/pipeline/graph/test_gap_analysis.py
git commit -m "feat: graph-native unreachable-framework-import gap analysis check"
```

---

### Task 12: Verification pass

- [ ] **Step 1: Run the full graph test suite**

Run: `uv run pytest tests/unit/pipeline/graph/ -v`
Expected: all tests pass (builder, entry_points, framework_detect, queries, migrate, gap_analysis, fixtures_regression)

- [ ] **Step 2: Run the full existing test suite to confirm no regressions**

Run: `uv run pytest -v`
Expected: all existing tests still pass (this phase adds new modules only, does not modify `scanner.py`/`ast_migrate.py`/`gap_analysis.py`)

- [ ] **Step 3: Run lint/type checks**

Run: `uv run ruff check superrobot/pipeline/graph/ tests/unit/pipeline/graph/`
Run: `uv run mypy superrobot/pipeline/graph/`
Expected: no errors (fix any that appear before proceeding)

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: address lint/type issues in graph package"
```

---

## Not in this plan (deferred to a follow-up plan per the spec)

- Cutting over `scan`/`transform`/`validate`'s CLI-facing commands to call the graph-based path by default (deferred until Task 8's regression gate and this plan's full verification pass have both run clean and been reviewed in real use).
- Migrating `_EnvRewriter` and the remaining `ast_migrate.py` transformers to `libcst` (Task 10 migrates `_ImportRewriter` only, as the representative first case).
- Wiring the new `check_unreachable_frameworks` check into the existing `run_gap_analysis()` aggregate function in `gap_analysis.py` (it exists standalone in this plan; integrating it into the main validate flow is part of cutover).
- Deleting `scanner.py`/`ast_migrate.py`/`gap_analysis.py`.
- Phase 2 (DR-styled UI companion) — entirely separate plan.
