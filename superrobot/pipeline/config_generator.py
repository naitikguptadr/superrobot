"""Deterministic config generation from AnalysisResult — Stage 3."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from superrobot.dr.platform_rules import (
    validate_flat_imports,
    validate_pyproject,
    validate_runtime_params,
)
from superrobot.engine.providers import LLM_CLIENT_SHIMS as _LLM_REWRITES
from superrobot.models.agent_config import AgentConfig
from superrobot.models.analysis_result import AnalysisResult, DrFramework
from superrobot.models.scan_result import ScanResult
from superrobot.pipeline.ast_migrate import MigrationReport, deep_migrate_source

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

MYAGENT_TEMPLATES: dict[DrFramework, str] = {
    DrFramework.LANGGRAPH: "myagent_langgraph.j2",
    DrFramework.CREWAI: "myagent_crewai.j2",
    DrFramework.LLAMAINDEX: "myagent_llamaindex.j2",
    DrFramework.NAT: "myagent_nat.j2",
    DrFramework.PYDANTIC_AI: "myagent_pydanticai.j2",
}

DR_BASE_REQUIREMENTS = [
    "datarobot",
    "datarobot-genai",
    "openai",
    "pydantic>=2.0",
]


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(),
        keep_trailing_newline=True,
    )


def generate_config(
    scan: ScanResult,
    analysis: AnalysisResult,
    agent_name: str = "my-agent",
) -> AgentConfig:
    """Build AgentConfig from scan + analysis."""
    config = AgentConfig.from_scan_and_analysis(scan, analysis, agent_name)
    pyproject_path = Path(scan.repo_path) / "pyproject.toml"
    if pyproject_path.exists():
        config.original_pyproject = pyproject_path.read_text()
    return config


_EXCLUDED_DIR_PARTS = {".venv", "node_modules", ".superrobot", ".git", "__pycache__", "tests"}
# names the generated bundle already claims
_RESERVED_MODULES = {"custom", "myagent", "dr_llm"}


def migrate_source_files(repo_path: str | Path) -> dict[str, str]:
    """Copy the source repo's Python modules into the bundle, DRUM-flattened."""
    files, _report = migrate_source_files_with_report(repo_path)
    return files


def migrate_source_files_with_report(
    repo_path: str | Path,
) -> tuple[dict[str, str], MigrationReport]:
    """Like migrate_source_files, but also returns an aggregated MigrationReport.

    DRUM merges agent/agent/ into a flat bundle, so tools/search.py becomes
    search.py. Deep AST pass rewrites imports, strips secret env defaults,
    and flags A2A gather / hardcoded prompts.
    """
    root = Path(repo_path).resolve()
    empty_report = MigrationReport()
    if not root.exists():
        return {}, empty_report

    sources: dict[str, Path] = {}  # dotted module path -> file
    for py_file in sorted(root.rglob("*.py")):
        if any(part in _EXCLUDED_DIR_PARTS for part in py_file.relative_to(root).parts):
            continue
        rel = py_file.relative_to(root)
        dotted = ".".join(rel.with_suffix("").parts)
        sources[dotted] = py_file

    # dotted module -> flat module name, avoiding collisions and reserved names
    flat_names: dict[str, str] = {}
    used: set[str] = set(_RESERVED_MODULES)
    for dotted in sources:
        stem = dotted.rsplit(".", maxsplit=1)[-1]
        flat = stem if stem not in used else dotted.replace(".", "_")
        while flat in used:
            flat = f"app_{flat}"
        used.add(flat)
        flat_names[dotted] = flat

    migrated: dict[str, str] = {}
    aggregate = MigrationReport()
    for dotted, py_file in sources.items():
        content = py_file.read_text(encoding="utf-8", errors="replace")
        content, report = deep_migrate_source(content, flat_names)
        # regex fallback for relative imports AST may miss after unparse churn
        content = _rewrite_imports(content, flat_names)
        content = _rewrite_llm_calls(content)
        aggregate.flat_imports += report.flat_imports
        aggregate.env_rewrites += report.env_rewrites
        aggregate.gather_warnings += report.gather_warnings
        aggregate.prompt_extractions += report.prompt_extractions
        aggregate.notes.extend(report.notes)
        migrated[f"agent/agent/{flat_names[dotted]}.py"] = content
    return migrated, aggregate


def flat_module_name(repo_path: str | Path, entry_file: str) -> str:
    """Flat module name the entry file gets after migration."""
    dotted = ".".join(Path(entry_file).with_suffix("").parts)
    root = Path(repo_path).resolve() if repo_path else None
    if root and root.exists():
        migrated = migrate_source_files(root)
        candidate = Path(entry_file).stem
        names = {Path(p).stem for p in migrated}
        if candidate in names:
            return candidate
        flat = dotted.replace(".", "_")
        if flat in names:
            return flat
    return Path(entry_file).stem


def _rewrite_imports(content: str, flat_names: dict[str, str]) -> str:
    """Rewrite imports of migrated modules to their flat DRUM names."""
    for dotted in sorted(flat_names, key=len, reverse=True):
        flat = flat_names[dotted]
        if dotted == flat:
            continue
        content = re.sub(
            rf"(^|\n)(\s*)from\s+{re.escape(dotted)}\s+import\s",
            rf"\1\2from {flat} import ",
            content,
        )

        def import_repl(m: re.Match[str], f: str = flat) -> str:
            return f"{m.group(1)}{m.group(2)}import {f}{m.group(3) or ''}"

        content = re.sub(
            rf"(^|\n)(\s*)import\s+{re.escape(dotted)}(\s+as\s+\w+)?(?=\s|$)",
            import_repl,
            content,
        )
    # relative imports become flat too: from .graph import X -> from graph import X
    content = re.sub(r"(^|\n)(\s*)from\s+\.(\w+)\s+import\s", r"\1\2from \3 import ", content)
    return content


def _rewrite_llm_calls(content: str) -> str:
    """Rewire LLM client constructor calls through the dr_llm gateway shim.

    ChatOpenAI(...) → dr_chat_openai(...) etc. On DR the shim routes through
    the DR LLM Gateway; off DR it constructs the original client unchanged.
    """
    used: list[str] = []
    for name, wrapper in _LLM_REWRITES.items():
        pattern = rf"(?<![\w.]){name}\s*\("
        if re.search(pattern, content):
            content = re.sub(pattern, f"{wrapper}(", content)
            used.append(wrapper)
    if not used:
        return content

    import_line = f"from dr_llm import {', '.join(sorted(used))}\n"
    return _insert_after_docstring(content, import_line)


def _insert_after_docstring(content: str, line: str) -> str:
    """Insert a line after the module docstring (or at the top)."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return line + content
    body = getattr(tree, "body", [])
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        lines = content.splitlines(keepends=True)
        idx = body[0].end_lineno or 1
        return "".join(lines[:idx]) + line + "".join(lines[idx:])
    return line + content


def render_files(config: AgentConfig) -> dict[str, str]:
    """Render all Jinja2 templates to a path→content dict."""
    jinja = _env()
    myagent_template = MYAGENT_TEMPLATES[config.dr_framework]
    merged_deps = _merge_dependencies(config.dependencies, config.original_pyproject)

    migrated = migrate_source_files(config.repo_path) if config.repo_path else {}
    entry_module = (
        flat_module_name(config.repo_path, config.entry_file)
        if migrated
        else Path(config.entry_file).stem
    )

    from superrobot.dr.llm_gateway import DEFAULT_MODEL

    ctx = {
        "config": config,
        "agent_name": config.agent_name,
        "agent_purpose": config.agent_purpose,
        "entry_file": config.entry_file,
        "entry_function": config.entry_function,
        "entry_module": entry_module,
        "entry_params": config.entry_params,
        "migrated": bool(migrated),
        "default_model": DEFAULT_MODEL,
        "runtime_param_keys": config.runtime_param_keys,
        "env_vars": config.env_vars,
        "env_var_descriptions": config.env_var_descriptions,
        "dependencies": merged_deps,
        "input_schema": config.input_schema,
        "output_schema": config.output_schema,
    }

    custom_py = jinja.get_template("custom_py.j2").render(**ctx)
    infra_py = jinja.get_template("infra_agent_py.j2").render(**ctx)
    env_template = jinja.get_template("env_template.j2").render(**ctx)
    pyproject = jinja.get_template("pyproject_toml.j2").render(**ctx)
    myagent = jinja.get_template(myagent_template).render(**ctx)
    workflow = jinja.get_template("workflow_yaml.j2").render(**ctx)
    agents_md = jinja.get_template("agents_md.j2").render(**ctx)

    if config.original_pyproject:
        validate_pyproject(config.original_pyproject, pyproject)

    violations: list[str] = []
    violations.extend(validate_flat_imports(myagent))
    violations.extend(validate_flat_imports(custom_py))
    violations.extend(validate_runtime_params(config, custom_py, infra_py, env_template))
    if violations:
        import warnings

        for v in violations:
            warnings.warn(v, stacklevel=2)

    files = {
        "agent/agent/custom.py": custom_py,
        "agent/agent/workflow.yaml": workflow,
        "agent/agent/myagent.py": myagent,
        "infra/infra/agent.py": infra_py,
        "pyproject.toml": pyproject,
        ".env.template": env_template,
        "AGENTS.md": agents_md,
    }
    # migrated business logic ships inside the bundle, DRUM-flat
    files.update(migrated)
    if migrated:
        # gateway shim so the migrated LLM calls run on DR without provider keys
        files["agent/agent/dr_llm.py"] = jinja.get_template("dr_llm_py.j2").render(**ctx)
        files["agent/agent/workload_service.py"] = jinja.get_template(
            "workload_service_py.j2"
        ).render(**ctx)
        files["workload/Dockerfile"] = jinja.get_template("workload_Dockerfile.j2").render(**ctx)
        files["workload/workload.yaml"] = jinja.get_template("workload_yaml.j2").render(**ctx)
    return files


def write_generated_files(files: dict[str, str], output_dir: str | Path) -> Path:
    """Write generated files to output directory."""
    out = Path(output_dir).resolve()
    for rel_path, content in files.items():
        dest = out / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    return out


def apply_fix(suggestion: str, config: AgentConfig) -> dict[str, str]:
    """Re-render templates after applying a copilot [FIX] suggestion."""
    if "flat import" in suggestion.lower() or "drum" in suggestion.lower():
        config.entry_file = Path(config.entry_file).name
    return render_files(config)


def _merge_dependencies(detected: list[str], original_pyproject: str) -> list[str]:
    merged: set[str] = set(DR_BASE_REQUIREMENTS)
    merged.update(detected)
    if original_pyproject:
        for line in original_pyproject.splitlines():
            stripped = line.strip().strip(",").strip('"').strip("'")
            if stripped and not stripped.startswith("#") and not stripped.startswith("["):
                pkg = stripped.split(">=")[0].split("==")[0]
                if pkg and pkg not in ("dependencies", "="):
                    merged.add(pkg)
    return sorted(merged)
