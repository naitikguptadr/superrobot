"""Merged config ready for Jinja2 templating."""

import re

from pydantic import BaseModel, Field

from superrobot.models.analysis_result import AnalysisResult, DrFramework
from superrobot.models.scan_result import ScanResult

_SIGNATURE = re.compile(r"def\s+\w+\((?P<args>[^)]*)\)")


def parse_signature_params(signature: str) -> list[str]:
    """Extract parameter names from a scanned signature string."""
    match = _SIGNATURE.search(signature or "")
    if not match:
        return []
    params = []
    for raw in match.group("args").split(","):
        name = raw.split(":")[0].split("=")[0].strip()
        if name and name not in ("self", "cls", "*", "/"):
            params.append(name.lstrip("*"))
    return params


class AgentConfig(BaseModel):
    """Final merged config for code generation."""

    agent_name: str = "my-agent"
    agent_purpose: str = ""
    dr_framework: DrFramework = DrFramework.LANGGRAPH
    entry_file: str = "main.py"
    entry_function: str = "run_agent"
    entry_params: list[str] = Field(default_factory=list)
    repo_path: str = ""
    dependencies: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)
    env_var_descriptions: dict[str, str] = Field(default_factory=dict)
    input_schema: dict[str, str] = Field(default_factory=dict)
    output_schema: dict[str, str] = Field(default_factory=dict)
    runtime_param_keys: list[str] = Field(default_factory=list)
    original_pyproject: str = ""

    @classmethod
    def from_scan_and_analysis(
        cls,
        scan: ScanResult,
        analysis: AnalysisResult,
        agent_name: str = "my-agent",
    ) -> "AgentConfig":
        """Merge scan and analysis into a generation-ready config."""
        entry = scan.primary_entry
        env_vars = sorted(set(scan.env_vars))
        return cls(
            agent_name=agent_name,
            agent_purpose=analysis.agent_purpose,
            dr_framework=analysis.dr_framework,
            entry_file=entry.file if entry else "main.py",
            entry_function=entry.function if entry else "run_agent",
            entry_params=parse_signature_params(entry.signature) if entry else [],
            repo_path=scan.repo_path,
            dependencies=scan.dependencies,
            env_vars=env_vars,
            env_var_descriptions={var: f"Required for {var}" for var in env_vars},
            input_schema=analysis.input_schema,
            output_schema=analysis.output_schema,
            runtime_param_keys=env_vars,
        )
