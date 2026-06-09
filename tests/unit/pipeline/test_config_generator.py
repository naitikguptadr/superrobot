"""Config generator snapshot tests."""

from pathlib import Path

from superrobot.models.analysis_result import AnalysisResult, DrFramework
from superrobot.models.scan_result import ScanResult
from superrobot.pipeline.config_generator import (
    generate_config,
    render_files,
    write_generated_files,
)

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def _make_config(
    framework: DrFramework = DrFramework.LANGGRAPH,
) -> tuple[ScanResult, AnalysisResult]:
    scan = ScanResult(
        detected_framework="langchain",
        env_vars=["OPENAI_API_KEY", "PROMPT_TEMPLATE_ID"],
        dependencies=["langchain", "openai"],
        confidence=0.9,
        repo_path=str(FIXTURES / "langchain_agent"),
    )
    analysis = AnalysisResult(
        agent_purpose="Research agent",
        dr_framework=framework,
        input_schema={"query": "str"},
        output_schema={"response": "str"},
        confidence=0.9,
    )
    return scan, analysis


def test_render_files_happy_path() -> None:
    scan, analysis = _make_config()
    config = generate_config(scan, analysis, agent_name="research-agent")
    files = render_files(config)
    assert "agent/agent/custom.py" in files
    assert "agent/agent/myagent.py" in files
    assert "OPENAI_API_KEY" in files["agent/agent/custom.py"]
    assert "PROMPT_TEMPLATE_ID" in files["agent/agent/myagent.py"]
    assert "from datarobot_genai import LangGraphAgent" in files["agent/agent/myagent.py"]


def test_render_files_all_frameworks() -> None:
    for framework in DrFramework:
        scan, analysis = _make_config(framework)
        analysis.dr_framework = framework
        config = generate_config(scan, analysis)
        files = render_files(config)
        assert "agent/agent/myagent.py" in files
        assert len(files) == 7


def test_write_generated_files(tmp_path: Path) -> None:
    scan, analysis = _make_config()
    config = generate_config(scan, analysis)
    files = render_files(config)
    out = write_generated_files(files, tmp_path)
    assert (out / "agent/agent/custom.py").exists()
    assert (out / "pyproject.toml").exists()
