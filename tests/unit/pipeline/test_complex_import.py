"""End-to-end generate for complex multi-module LangGraph fixture."""

from __future__ import annotations

import asyncio
from pathlib import Path

from superrobot.pipeline.analyzer import analyze
from superrobot.pipeline.config_generator import generate_config, render_files
from superrobot.pipeline.scanner import scan

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "langgraph_research_agent"


def test_complex_langgraph_agent_generates_migrated_bundle() -> None:
    scan_result = scan(FIXTURE)
    analysis = asyncio.run(analyze(scan_result))
    config = generate_config(scan_result, analysis)
    files = render_files(config)

    myagent = files["agent/agent/myagent.py"]
    assert "from main import run_agent" in myagent
    assert "TODO" not in myagent
    assert "agent/agent/graph.py" in files
    assert "agent/agent/search.py" in files
    assert "from graph import build_graph" in files["agent/agent/main.py"]
    assert "agent/agent/dr_llm.py" not in files or "dr_llm" not in files["agent/agent/main.py"]
