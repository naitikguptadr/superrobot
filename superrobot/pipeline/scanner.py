"""Static repo analysis — Stage 1."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from superrobot.models.scan_result import EntryPoint, RiskFlag, ScanResult

FRAMEWORK_IMPORTS: dict[str, str] = {
    "langchain": "langchain",
    "langchain_core": "langchain",
    "langgraph": "langgraph",
    "crewai": "crewai",
    "llama_index": "llamaindex",
    "pydantic_ai": "pydantic_ai",
}

LLM_PATTERNS = ("ChatOpenAI", "chat.completions", "self.llm(")
TOOL_PATTERNS = ("@tool", "@mcp.tool")
SECRET_PATTERNS = re.compile(
    r'(api[_-]?key|secret|password|token)\s*=\s*["\'][^{][^"\']+["\']',
    re.IGNORECASE,
)
GETENV_PATTERN = re.compile(r'os\.(?:environ\.get|getenv)\s*\(\s*["\'](\w+)["\']')
ENTRY_POINT_NAMES = ("run", "run_agent", "main", "invoke", "process", "execute")


def scan(repo_path: str | Path) -> ScanResult:
    """Scan a foreign agent repo and return ScanResult."""
    root = Path(repo_path).resolve()
    if not root.exists():
        msg = f"Repo path does not exist: {root}"
        raise FileNotFoundError(msg)

    py_files = [
        f
        for f in root.rglob("*.py")
        if not any(part in (".venv", "node_modules", ".superrobot", ".git") for part in f.parts)
    ]

    detected_framework = "unknown"
    has_state_graph = False
    entry_points: list[EntryPoint] = []
    env_vars: set[str] = set()
    risk_flags: list[RiskFlag] = []
    dependencies = _read_dependencies(root)

    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if SECRET_PATTERNS.search(source) and RiskFlag.HARDCODED_SECRET not in risk_flags:
            risk_flags.append(RiskFlag.HARDCODED_SECRET)

        nested = "from agent.agent." in source or "import agent.agent." in source
        if nested and RiskFlag.NESTED_IMPORTS not in risk_flags:
            risk_flags.append(RiskFlag.NESTED_IMPORTS)

        env_vars.update(GETENV_PATTERN.findall(source))

        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for prefix, framework in FRAMEWORK_IMPORTS.items():
                    if node.module.startswith(prefix):
                        detected_framework = framework
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for prefix, framework in FRAMEWORK_IMPORTS.items():
                        if alias.name.startswith(prefix):
                            detected_framework = framework

            if isinstance(node, ast.Name) and node.id == "StateGraph":
                has_state_graph = True

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                rel = str(py_file.relative_to(root))
                is_async = isinstance(node, ast.AsyncFunctionDef)
                if node.name in ENTRY_POINT_NAMES or (is_async and node.name.startswith("run_")):
                    sig = _format_signature(node)
                    entry_points.append(EntryPoint(file=rel, function=node.name, signature=sig))

    for dep in dependencies:
        for pkg, framework in {
            "langchain": "langchain",
            "langgraph": "langgraph",
            "crewai": "crewai",
            "llama-index": "llamaindex",
            "pydantic-ai": "pydantic_ai",
        }.items():
            if pkg in dep.lower() and detected_framework == "unknown":
                detected_framework = framework

    if (root / "workflow.yaml").exists():
        detected_framework = "nat"

    if detected_framework == "unknown" and _has_raw_async(py_files):
        detected_framework = "raw_async"

    if not (root / ".env.example").exists() and not (root / ".env.template").exists():
        risk_flags.append(RiskFlag.MISSING_ENV_EXAMPLE)

    confidence = _compute_confidence(detected_framework, has_state_graph, entry_points)

    return ScanResult(
        detected_framework=detected_framework,
        entry_points=entry_points,
        dependencies=dependencies,
        env_vars=sorted(env_vars),
        input_signatures=[ep.signature for ep in entry_points if ep.signature],
        risk_flags=risk_flags,
        confidence=confidence,
        repo_path=str(root),
    )


# Constructors / attribute chains that represent an actual LLM client
_LLM_CONSTRUCTORS = {
    "ChatOpenAI",
    "AzureChatOpenAI",
    "OpenAI",
    "AsyncOpenAI",
    "ChatAnthropic",
    "Anthropic",
    "ChatVertexAI",
    "ChatBedrock",
}
_MAX_GRAPH_NODES = 12

GraphData = tuple[list[dict[str, str]], list[tuple[str, str]]]


def build_graph(repo_path: str | Path) -> GraphData:
    """Build the agent execution graph (nodes + edges) for the TUI graph panel.

    LangGraph repos get the real graph from StateGraph add_node/add_edge calls.
    Everything else gets a heuristic flow: Input → entry point → LLM clients /
    @tool functions / memory components → Output.
    """
    root = Path(repo_path).resolve()
    trees: list[ast.AST] = []
    for py_file in root.rglob("*.py"):
        if any(part in (".venv", "node_modules", ".superrobot", ".git") for part in py_file.parts):
            continue
        try:
            trees.append(ast.parse(py_file.read_text(encoding="utf-8", errors="replace")))
        except (OSError, SyntaxError):
            continue

    for tree in trees:
        state_graph = _extract_stategraph(tree)
        if state_graph is not None:
            return state_graph

    return _heuristic_graph(trees)


def build_graph_nodes(repo_path: str | Path) -> list[dict[str, str]]:
    """Nodes-only view of build_graph (kept for callers that infer edges)."""
    return build_graph(repo_path)[0]


def _extract_stategraph(tree: ast.AST) -> GraphData | None:
    """Recover the real graph from StateGraph add_node / add_edge calls."""
    node_names: list[str] = []
    edges: list[tuple[str, str]] = []
    routers: set[str] = set()

    def edge_endpoint(arg: ast.expr) -> str | None:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.Name) and arg.id in ("START", "END"):
            return arg.id
        return None

    # routing functions return node names as string constants — collect them so
    # add_conditional_edges(src, fn) can be resolved to real edges
    returns_by_func: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            returned: set[str] = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    # walk the whole expression: handles ternaries, dict lookups
                    for expr in ast.walk(sub.value):
                        if isinstance(expr, ast.expr):
                            target = edge_endpoint(expr)
                            if target:
                                returned.add(target)
            returns_by_func[node.name] = returned

    def conditional_targets(call: ast.Call) -> set[str]:
        targets: set[str] = set()
        for arg in call.args[1:]:
            if isinstance(arg, ast.Name) and arg.id in returns_by_func:
                targets.update(returns_by_func[arg.id])
            elif isinstance(arg, ast.Dict):  # path map {"key": "node"}
                for value in arg.values:
                    target = edge_endpoint(value)
                    if target:
                        targets.add(target)
        return targets

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        method = node.func.attr
        if method == "add_node" and node.args:
            name = edge_endpoint(node.args[0])
            if name:
                node_names.append(name)
        elif method == "add_edge" and len(node.args) >= 2:
            src, dst = edge_endpoint(node.args[0]), edge_endpoint(node.args[1])
            if src and dst:
                edges.append((src, dst))
        elif method == "add_conditional_edges" and node.args:
            src = edge_endpoint(node.args[0])
            if src:
                routers.add(src)
                for target in conditional_targets(node):
                    if target != src:
                        edges.append((src, target))
        elif method == "set_entry_point" and node.args:
            name = edge_endpoint(node.args[0])
            if name:
                edges.append(("START", name))

    if not node_names:
        return None

    nodes = [{"id": "input", "label": "Input", "type": "input"}]
    for name in dict.fromkeys(node_names):
        node_type = "router" if name in routers else "llm_call"
        nodes.append({"id": name, "label": name, "type": node_type})
    nodes.append({"id": "output", "label": "Output", "type": "output"})

    renamed = [
        (
            src.replace("START", "input").replace("END", "output"),
            dst.replace("START", "input").replace("END", "output"),
        )
        for src, dst in edges
    ]
    known = {n["id"] for n in nodes}
    graph_edges = [(s, d) for s, d in renamed if s in known and d in known]
    return nodes, graph_edges


def _heuristic_graph(trees: list[ast.AST]) -> GraphData:
    """Input → entry function → LLM / tools / memory → Output."""
    entry: str | None = None
    llm_labels: list[str] = []
    tool_labels: list[str] = []
    memory_labels: list[str] = []

    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if entry is None and (
                    node.name in ENTRY_POINT_NAMES or node.name.startswith("run_")
                ):
                    entry = node.name
                for decorator in node.decorator_list:
                    dec = decorator.func if isinstance(decorator, ast.Call) else decorator
                    dec_name = getattr(dec, "attr", None) or getattr(dec, "id", None)
                    if dec_name == "tool":
                        tool_labels.append(node.name)
            elif isinstance(node, ast.Call):
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if not name:
                    continue
                if name in _LLM_CONSTRUCTORS:
                    llm_labels.append(name)
                elif "Memory" in name:
                    memory_labels.append(name)

    nodes: list[dict[str, str]] = [{"id": "input", "label": "Input", "type": "input"}]
    edges: list[tuple[str, str]] = []
    previous = "input"
    if entry:
        nodes.append({"id": entry, "label": f"{entry}()", "type": "router"})
        edges.append(("input", entry))
        previous = entry

    middle: list[tuple[str, str]] = [(label, "llm_call") for label in dict.fromkeys(llm_labels)]
    middle += [(label, "tool") for label in dict.fromkeys(tool_labels)]
    middle += [(label, "memory_read") for label in dict.fromkeys(memory_labels)]
    middle = middle[: _MAX_GRAPH_NODES - len(nodes) - 1]

    for label, node_type in middle:
        nodes.append({"id": label, "label": label, "type": node_type})
        edges.append((previous, label))
    for label, _ in middle:
        edges.append((label, "output"))
    if not middle:
        edges.append((previous, "output"))

    nodes.append({"id": "output", "label": "Output", "type": "output"})
    return nodes, edges


def _read_dependencies(root: Path) -> list[str]:
    deps: list[str] = []
    req = root / "requirements.txt"
    if req.exists():
        for line in req.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                deps.append(line.split("==")[0].split(">=")[0].strip())

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text()
        in_deps = False
        for line in content.splitlines():
            if "dependencies" in line and "=" in line:
                in_deps = True
                continue
            if in_deps:
                if line.strip().startswith("]"):
                    break
                stripped = line.strip().strip(",").strip('"').strip("'")
                if stripped and not stripped.startswith("#"):
                    pkg = stripped.split(">=")[0].split("==")[0].strip('"').strip("'")
                    if pkg:
                        deps.append(pkg)
    return deps


def _has_raw_async(py_files: list[Path]) -> bool:
    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            if "async def" in source and ("httpx" in source or "aiohttp" in source):
                return True
        except OSError:
            continue
    return False


def _format_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [a.arg for a in node.args.args]
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}def {node.name}({', '.join(args)})"


def _compute_confidence(
    framework: str,
    has_state_graph: bool,
    entry_points: list[EntryPoint],
) -> float:
    base = {
        "langgraph": 0.9,
        "crewai": 0.9,
        "llamaindex": 0.9,
        "pydantic_ai": 0.9,
        "nat": 0.95,
        "langchain": 0.7 if not has_state_graph else 0.9,
        "raw_async": 0.4,
        "unknown": 0.2,
    }.get(framework, 0.3)
    if entry_points:
        base = min(1.0, base + 0.1)
    return base
