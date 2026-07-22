"""Engine unit tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from superrobot.engine.pipeline import TransformEngine
from superrobot.engine.providers import (
    LLM_CLIENT_SHIMS,
    LLM_CONSTRUCTORS,
    detect_providers_from_imports,
)


def test_llm_registry_covers_shims() -> None:
    assert frozenset(LLM_CLIENT_SHIMS) == LLM_CONSTRUCTORS
    assert "ChatAnthropic" in LLM_CONSTRUCTORS
    assert "ChatGroq" in LLM_CONSTRUCTORS


def test_detect_providers_from_imports() -> None:
    assert "openai" in detect_providers_from_imports("langchain_openai")
    assert "anthropic" in detect_providers_from_imports("langchain_anthropic")


def test_transform_engine_scan_fixture() -> None:
    fixture = Path(__file__).parent.parent.parent / "fixtures" / "langchain_agent"
    engine = TransformEngine()
    result = engine.run_scan(str(fixture))
    assert result.detected_framework in ("langchain", "langgraph")
    assert result.graph_nodes


def test_transform_engine_headless_generate(tmp_path: Path) -> None:
    fixture = Path(__file__).parent.parent.parent / "fixtures" / "langchain_agent"

    async def run() -> None:
        engine = TransformEngine()
        ctx = await engine.transform(
            str(fixture),
            output_dir=tmp_path / "out",
            skip_eval=True,
            skip_deploy=True,
            skip_clone=True,
        )
        assert ctx.scan is not None
        assert ctx.analysis is not None
        assert (ctx.output_dir / "agent/agent/myagent.py").is_file()

    asyncio.run(run())
