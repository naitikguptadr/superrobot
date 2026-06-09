"""Scanner unit tests."""

from pathlib import Path

import pytest

from superrobot.pipeline.scanner import scan

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def test_scan_langchain_agent_happy_path() -> None:
    result = scan(FIXTURES / "langchain_agent")
    assert result.detected_framework in ("langchain", "langgraph")
    assert result.confidence >= 0.5
    assert any(ep.function == "run_agent" for ep in result.entry_points)
    assert "OPENAI_API_KEY" in result.env_vars
    assert "langchain" in result.dependencies or "langchain-openai" in result.dependencies


def test_scan_crewai_agent() -> None:
    result = scan(FIXTURES / "crewai_agent")
    assert result.detected_framework == "crewai"
    assert result.confidence >= 0.8


def test_scan_missing_path_raises() -> None:
    with pytest.raises(FileNotFoundError):
        scan("/nonexistent/path/to/repo")


def test_scan_raw_async_low_confidence() -> None:
    result = scan(FIXTURES / "raw_async_agent")
    assert result.detected_framework == "raw_async"
    assert result.confidence <= 0.6


def test_scan_llamaindex_agent() -> None:
    result = scan(FIXTURES / "llamaindex_agent")
    assert result.detected_framework == "llamaindex"
