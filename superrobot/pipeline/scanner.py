"""Static repo analysis — Stage 1."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from superrobot.engine.providers import (
    LLM_CONSTRUCTORS,
    detect_providers_from_imports,
    provider_env_hints,
)
from superrobot.models.scan_result import EntryPoint, RiskFlag, ScanResult

SKIP_DIR_PARTS = frozenset(
    {".venv", "node_modules", ".superrobot", ".git", "__pycache__", "tests", "test", ".tox", "dist"}
)

FRAMEWORK_IMPORTS: dict[str, str] = {
    "langchain": "langchain",
    "langchain_core": "langchain",
    "langchain_openai": "langchain",
    "langchain_community": "langchain",
    "langgraph": "langgraph",
    "crewai": "crewai",
    "llama_index": "llamaindex",
    "pydantic_ai": "pydantic_ai",
    # AutoGen family (AG2 / pyautogen / autogen_agentchat)
    "autogen": "autogen",
    "autogen_agentchat": "autogen",
    "autogen_core": "autogen",
    "autogen_ext": "autogen",
    "pyautogen": "autogen",
    # Microsoft Semantic Kernel
    "semantic_kernel": "semantic_kernel",
    # Haystack
    "haystack": "haystack",
    "haystack_integrations": "haystack",
    # OpenAI Agents SDK (avoid bare "agents" — too many false positives)
    "openai_agents": "openai_agents",
    "agents.agent": "openai_agents",
    "agents.run": "openai_agents",
    "agents.tool": "openai_agents",
    # SmolaAgents (HF)
    "smolagents": "smolagents",
    # Google ADK
    "google.adk": "google_adk",
    "google_adk": "google_adk",
}

DEPENDENCY_FRAMEWORKS: dict[str, str] = {
    "langchain": "langchain",
    "langgraph": "langgraph",
    "crewai": "crewai",
    "llama-index": "llamaindex",
    "pydantic-ai": "pydantic_ai",
    "pyautogen": "autogen",
    "autogen-agentchat": "autogen",
    "ag2": "autogen",
    "semantic-kernel": "semantic_kernel",
    "haystack-ai": "haystack",
    "haystack": "haystack",
    "openai-agents": "openai_agents",
    "smolagents": "smolagents",
    "google-adk": "google_adk",
}

# Class / call-site patterns that confirm a framework even without package imports
FRAMEWORK_SYMBOLS: dict[str, str] = {
    "AssistantAgent": "autogen",
    "UserProxyAgent": "autogen",
    "ConversableAgent": "autogen",
    "GroupChat": "autogen",
    "Kernel": "semantic_kernel",
    "ChatCompletionAgent": "semantic_kernel",
    "Pipeline": "haystack",
    "Agent": "openai_agents",  # weak — also used elsewhere; gated by import
    "CodeAgent": "smolagents",
    "ToolCallingAgent": "smolagents",
    "LlmAgent": "google_adk",
}

SECRET_PATTERNS = re.compile(
    r'(api[_-]?key|secret|password|token)\s*=\s*["\'][^{][^"\']+["\']',
    re.IGNORECASE,
)
GETENV_PATTERN = re.compile(r'os\.(?:environ\.get|getenv)\s*\(\s*["\'](\w+)["\']')
ENTRY_POINT_NAMES = frozenset(
    {"run", "run_agent", "main", "invoke", "process", "execute", "handle", "chat", "query"}
)
ENTRY_PRIORITY: dict[str, int] = {
    "run_agent": 100,
    "run": 90,
    "invoke": 80,
    "process": 70,
    "handle": 65,
    "chat": 60,
    "query": 55,
    "main": 50,
    "execute": 45,
}

_MAX_GRAPH_NODES = 16

GraphData = tuple[list[dict[str, str]], list[tuple[str, str]]]


def scan(repo_path: str | Path) -> ScanResult:
    """Scan a foreign agent repo and return ScanResult."""
    root = Path(repo_path).resolve()
    if not root.exists():
        msg = f"Repo path does not exist: {root}"
        raise FileNotFoundError(msg)

    py_files = _collect_python_files(root)
    dependencies = _read_dependencies(root)

    detected_framework = "unknown"
    has_state_graph = False
    entry_points: list[EntryPoint] = []
    env_vars: set[str] = set()
    risk_flags: list[RiskFlag] = []
    tools: list[str] = []
    llm_clients: list[str] = []
    providers: set[str] = set()
    trees: list[ast.AST] = []

    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if SECRET_PATTERNS.search(source) and RiskFlag.HARDCODED_SECRET not in risk_flags:
            risk_flags.append(RiskFlag.HARDCODED_SECRET)

        if ("from agent.agent." in source or "import agent.agent." in source) and (
            RiskFlag.NESTED_IMPORTS not in risk_flags
        ):
            risk_flags.append(RiskFlag.NESTED_IMPORTS)

        env_vars.update(GETENV_PATTERN.findall(source))

        try:
            tree = ast.parse(source)
            trees.append(tree)
        except SyntaxError:
            continue

        rel = str(py_file.relative_to(root))
        for node in ast.walk(tree):
            detected_framework, has_state_graph = _inspect_imports(
                node, detected_framework, has_state_graph
            )
            if detected_framework == "unknown":
                detected_framework = _inspect_symbols(node, detected_framework)
            if isinstance(node, ast.ImportFrom) and node.module:
                providers.update(detect_providers_from_imports(node.module))
            _collect_entry_points(node, rel, entry_points)
            _collect_tools(node, tools)
            _collect_llm_clients(node, llm_clients)

    for dep in dependencies:
        for pkg, framework in DEPENDENCY_FRAMEWORKS.items():
            if pkg in dep.lower() and detected_framework == "unknown":
                detected_framework = framework

    if (root / "workflow.yaml").exists():
        detected_framework = "nat"

    if has_state_graph and detected_framework in ("unknown", "langchain"):
        detected_framework = "langgraph"

    if detected_framework == "unknown" and _has_raw_async(py_files):
        detected_framework = "raw_async"

    if not (root / ".env.example").exists() and not (root / ".env.template").exists():
        risk_flags.append(RiskFlag.MISSING_ENV_EXAMPLE)

    ranked_entries = _rank_entry_points(entry_points)
    graph_nodes, graph_edges = _build_graph_from_trees(trees)

    # Merge provider-specific env vars into scan for template generation
    for var in provider_env_hints(providers):
        env_vars.add(var)

    confidence = _compute_confidence(detected_framework, has_state_graph, ranked_entries)

    return ScanResult(
        detected_framework=detected_framework,
        entry_points=ranked_entries,
        dependencies=dependencies,
        env_vars=sorted(env_vars),
        input_signatures=[ep.signature for ep in ranked_entries if ep.signature],
        risk_flags=risk_flags,
        confidence=confidence,
        repo_path=str(root),
        has_state_graph=has_state_graph,
        tools=sorted(dict.fromkeys(tools)),
        llm_clients=sorted(dict.fromkeys(llm_clients)),
        detected_providers=sorted(providers),
        graph_nodes=graph_nodes,
        graph_edges=graph_edges,
        python_file_count=len(py_files),
    )


def build_graph(repo_path: str | Path) -> GraphData:
    """Build agent execution graph — prefers cached scan when possible."""
    result = scan(repo_path)
    return result.graph_nodes, result.graph_edges


def build_graph_nodes(repo_path: str | Path) -> list[dict[str, str]]:
    """Nodes-only view of build_graph."""
    return build_graph(repo_path)[0]


def _collect_python_files(root: Path) -> list[Path]:
    return [
        f
        for f in root.rglob("*.py")
        if not any(part in SKIP_DIR_PARTS for part in f.relative_to(root).parts)
    ]


def _inspect_imports(
    node: ast.AST,
    detected_framework: str,
    has_state_graph: bool,
) -> tuple[str, bool]:
    if isinstance(node, ast.ImportFrom) and node.module:
        for prefix, framework in FRAMEWORK_IMPORTS.items():
            if node.module == prefix or node.module.startswith(prefix + "."):
                # Prefer more specific frameworks over generic "agents" package
                if framework == "openai_agents" and detected_framework not in (
                    "unknown",
                    "raw_async",
                ):
                    continue
                detected_framework = framework
    elif isinstance(node, ast.Import):
        for alias in node.names:
            for prefix, framework in FRAMEWORK_IMPORTS.items():
                if alias.name == prefix or alias.name.startswith(prefix + "."):
                    if framework == "openai_agents" and detected_framework not in (
                        "unknown",
                        "raw_async",
                    ):
                        continue
                    detected_framework = framework
    if isinstance(node, ast.Name) and node.id == "StateGraph":
        has_state_graph = True
    if isinstance(node, ast.Attribute) and node.attr == "StateGraph":
        has_state_graph = True
    return detected_framework, has_state_graph


def _inspect_symbols(node: ast.AST, detected_framework: str) -> str:
    """Confirm framework from well-known class/call names when still unknown."""
    if detected_framework != "unknown":
        return detected_framework
    name: str | None = None
    if isinstance(node, ast.Name):
        name = node.id
    elif isinstance(node, ast.Attribute):
        name = node.attr
    elif isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
    if name and name in FRAMEWORK_SYMBOLS:
        # Skip ultra-generic "Agent" symbol — too many false positives
        if name == "Agent":
            return detected_framework
        return FRAMEWORK_SYMBOLS[name]
    return detected_framework


def _collect_entry_points(
    node: ast.AST,
    rel_path: str,
    entry_points: list[EntryPoint],
) -> None:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return
    is_async = isinstance(node, ast.AsyncFunctionDef)
    if node.name in ENTRY_POINT_NAMES or (is_async and node.name.startswith("run_")):
        sig = _format_signature(node)
        entry_points.append(EntryPoint(file=rel_path, function=node.name, signature=sig))


def _collect_tools(node: ast.AST, tools: list[str]) -> None:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return
    for decorator in node.decorator_list:
        dec = decorator.func if isinstance(decorator, ast.Call) else decorator
        dec_name = getattr(dec, "attr", None) or getattr(dec, "id", None)
        if dec_name in ("tool", "mcp_tool"):
            tools.append(node.name)


def _collect_llm_clients(node: ast.AST, llm_clients: list[str]) -> None:
    if not isinstance(node, ast.Call):
        return
    name: str | None = None
    if isinstance(node.func, ast.Name):
        name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        name = node.func.attr
    if name and name in LLM_CONSTRUCTORS:
        llm_clients.append(name)


def _rank_entry_points(entry_points: list[EntryPoint]) -> list[EntryPoint]:
    def score(ep: EntryPoint) -> int:
        value = ENTRY_PRIORITY.get(ep.function, 0)
        if ep.file in ("main.py", "app.py", "__main__.py", "agent.py"):
            value += 20
        if ep.function.startswith("run_"):
            value += 10
        if ep.signature.startswith("async "):
            value += 5
        return value

    return sorted(entry_points, key=score, reverse=True)


def _build_graph_from_trees(trees: list[ast.AST]) -> GraphData:
    for tree in trees:
        state_graph = _extract_stategraph(tree)
        if state_graph is not None:
            return state_graph
    return _heuristic_graph(trees)


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

    returns_by_func: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            returned: set[str] = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None:
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
            elif isinstance(arg, ast.Dict):
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
                    if dec_name in ("tool", "mcp_tool"):
                        tool_labels.append(node.name)
            elif isinstance(node, ast.Call):
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if not name:
                    continue
                if name in LLM_CONSTRUCTORS:
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
        "langchain": 0.75 if not has_state_graph else 0.9,
        "autogen": 0.85,
        "semantic_kernel": 0.85,
        "haystack": 0.8,
        "openai_agents": 0.8,
        "smolagents": 0.8,
        "google_adk": 0.8,
        "raw_async": 0.4,
        "unknown": 0.2,
    }.get(framework, 0.3)
    if entry_points:
        base = min(1.0, base + 0.1)
    if has_state_graph:
        base = min(1.0, base + 0.05)
    return base
