"""Analyzer unit tests with mocked LLM."""

import pytest
from pytest_mock import MockerFixture

from superrobot.models.analysis_result import AnalysisResult, DrFramework
from superrobot.models.scan_result import ScanResult
from superrobot.pipeline.analyzer import _fallback_analysis, analyze


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
    scan = ScanResult(detected_framework="unknown", confidence=0.2)
    result = _fallback_analysis(scan)
    assert result.dr_framework == DrFramework.LANGGRAPH
    assert result.confidence <= 0.3


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
