"""Merged config ready for Jinja2 templating."""

from pydantic import BaseModel, Field

from superrobot.models.analysis_result import AnalysisResult, DrFramework
from superrobot.models.scan_result import ScanResult


class AgentConfig(BaseModel):
    """Final merged config for code generation."""

    agent_name: str = "my-agent"
    agent_purpose: str = ""
    dr_framework: DrFramework = DrFramework.LANGGRAPH
    entry_file: str = "main.py"
    entry_function: str = "run_agent"
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
        entry = scan.entry_points[0] if scan.entry_points else None
        env_vars = sorted(set(scan.env_vars))
        return cls(
            agent_name=agent_name,
            agent_purpose=analysis.agent_purpose,
            dr_framework=analysis.dr_framework,
            entry_file=entry.file if entry else "main.py",
            entry_function=entry.function if entry else "run_agent",
            dependencies=scan.dependencies,
            env_vars=env_vars,
            env_var_descriptions={var: f"Required for {var}" for var in env_vars},
            input_schema=analysis.input_schema,
            output_schema=analysis.output_schema,
            runtime_param_keys=env_vars,
        )
