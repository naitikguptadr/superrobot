"""Analyzer unit tests with mocked LLM."""

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from superrobot.models.analysis_result import AnalysisResult, DrFramework
from superrobot.models.scan_result import ScanResult
from superrobot.pipeline.analyzer import _fallback_analysis, analyze
from superrobot.pipeline.scanner import scan

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


@pytest.fixture
def sample_scan() -> ScanResult:
    return ScanResult(
        detected_framework="crewai",
        env_vars=["OPENAI_API_KEY"],
        confidence=0.9,
        repo_path="/tmp/test",
    )


def test_fallback_analysis_happy_path(sample_scan: ScanResult) -> None:
    result = _fallback_analysis(sample_scan)
    assert result.dr_framework == DrFramework.CREWAI
    assert result.agent_purpose
    assert result.confidence > 0


def test_fallback_analysis_unknown_framework() -> None:
    scan_result = ScanResult(detected_framework="unknown", confidence=0.2)
    result = _fallback_analysis(scan_result)
    assert result.dr_framework == DrFramework.LANGGRAPH
    assert result.confidence <= 0.3


def test_fallback_analysis_infers_complex_agent_schemas() -> None:
    scan_result = scan(FIXTURES / "langgraph_research_agent")
    result = _fallback_analysis(scan_result)
    assert result.dr_framework == DrFramework.LANGGRAPH
    assert "query" in result.input_schema
    assert "web_search" in scan_result.tools
    assert result.missing_requirements  # no PROMPT_TEMPLATE_ID in fixture


@pytest.mark.asyncio
async def test_analyze_uses_fallback_without_credentials(
    sample_scan: ScanResult,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPERROBOT_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("DATAROBOT_API_TOKEN", raising=False)
    monkeypatch.delenv("DATAROBOT_ENDPOINT", raising=False)
    result = await analyze(sample_scan)
    assert isinstance(result, AnalysisResult)
    assert result.dr_framework == DrFramework.CREWAI
    assert "schema inference" in result.notes or "LLM unavailable" in result.notes


@pytest.mark.asyncio
async def test_analyze_uses_fallback_on_llm_failure(
    sample_scan: ScanResult,
    mocker: MockerFixture,
) -> None:
    mock_gateway = mocker.Mock()
    mock_gateway.call = mocker.AsyncMock(side_effect=Exception("LLM unavailable"))
    result = await analyze(sample_scan, gateway=mock_gateway)
    assert isinstance(result, AnalysisResult)
    assert result.dr_framework == DrFramework.CREWAI
