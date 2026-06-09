"""Integration tests — require real LLM Gateway."""

import pytest

from superrobot.pipeline.analyzer import analyze
from superrobot.pipeline.config_generator import generate_config, render_files
from superrobot.pipeline.scanner import scan

FIXTURE = "tests/fixtures/langchain_agent"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_import_pipeline() -> None:
    scan_result = scan(FIXTURE)
    assert scan_result.confidence > 0
    analysis = await analyze(scan_result)
    assert analysis.agent_purpose
    config = generate_config(scan_result, analysis)
    files = render_files(config)
    assert len(files) == 7
