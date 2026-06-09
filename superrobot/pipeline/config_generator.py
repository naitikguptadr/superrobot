"""Deterministic config generation from AnalysisResult — Stage 3."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from superrobot.dr.platform_rules import (
    validate_flat_imports,
    validate_pyproject,
    validate_runtime_params,
)
from superrobot.models.agent_config import AgentConfig
from superrobot.models.analysis_result import AnalysisResult, DrFramework
from superrobot.models.scan_result import ScanResult

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


def render_files(config: AgentConfig) -> dict[str, str]:
    """Render all Jinja2 templates to a path→content dict."""
    jinja = _env()
    myagent_template = MYAGENT_TEMPLATES[config.dr_framework]
    merged_deps = _merge_dependencies(config.dependencies, config.original_pyproject)

    ctx = {
        "config": config,
        "agent_name": config.agent_name,
        "agent_purpose": config.agent_purpose,
        "entry_file": config.entry_file,
        "entry_function": config.entry_function,
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

    return {
        "agent/agent/custom.py": custom_py,
        "agent/agent/workflow.yaml": workflow,
        "agent/agent/myagent.py": myagent,
        "infra/infra/agent.py": infra_py,
        "pyproject.toml": pyproject,
        ".env.template": env_template,
        "AGENTS.md": agents_md,
    }


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
