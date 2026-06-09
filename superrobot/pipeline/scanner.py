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

    py_files = list(root.rglob("*.py"))
    py_files = [f for f in py_files if ".venv" not in f.parts and "node_modules" not in f.parts]

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


def build_graph_nodes(repo_path: str | Path) -> list[dict[str, str]]:
    """Extract graph nodes from AST for the TUI graph panel."""
    root = Path(repo_path).resolve()
    nodes: list[dict[str, str]] = [{"id": "input", "label": "Input", "type": "input"}]

    for py_file in root.rglob("*.py"):
        if ".venv" in py_file.parts:
            continue
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                label = _call_label(node, source)
                if label:
                    node_type = "llm_call" if any(p in label for p in LLM_PATTERNS) else "tool"
                    if any(p in source for p in TOOL_PATTERNS):
                        node_type = "tool"
                    nodes.append(
                        {
                            "id": f"{py_file.stem}_{label}",
                            "label": label[:30],
                            "type": node_type,
                        }
                    )

    nodes.append({"id": "output", "label": "Output", "type": "output"})
    return nodes


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


def _call_label(node: ast.Call, source: str) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None
