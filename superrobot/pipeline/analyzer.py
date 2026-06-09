"""LLM analysis of ScanResult — Stage 2."""

from __future__ import annotations

import json

from superrobot.dr.framework_mapper import map_framework
from superrobot.dr.llm_gateway import LLMGateway
from superrobot.models.analysis_result import AnalysisResult
from superrobot.models.scan_result import ScanResult


async def analyze(
    scan_result: ScanResult,
    gateway: LLMGateway | None = None,
) -> AnalysisResult:
    """Analyze a ScanResult via LLM Gateway, with framework mapper fallback."""
    gw = gateway or LLMGateway()
    user_content = json.dumps(scan_result.model_dump(), indent=2)

    try:
        result = await gw.call("analyze", user_content, AnalysisResult)
        return result
    except Exception:
        return _fallback_analysis(scan_result)


def _fallback_analysis(scan: ScanResult) -> AnalysisResult:
    """Deterministic fallback when LLM is unavailable."""
    has_state_graph = scan.detected_framework == "langgraph"
    has_workflow = scan.detected_framework == "nat"
    dr_framework, confidence = map_framework(
        scan.detected_framework,
        has_state_graph=has_state_graph,
        has_workflow_yaml=has_workflow,
    )
    return AnalysisResult(
        agent_purpose=f"Agent detected as {scan.detected_framework} framework",
        dr_framework=dr_framework,
        input_schema={"query": "str"},
        output_schema={"response": "str"},
        suggested_ui_components=["TextInput", "Card"],
        missing_requirements=[],
        risk_flags=[f.value for f in scan.risk_flags],
        notes="LLM unavailable; using deterministic framework mapper",
        confidence=min(confidence, scan.confidence),
    )
