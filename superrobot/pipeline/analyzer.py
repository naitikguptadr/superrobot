"""LLM analysis of ScanResult — Stage 2."""

from __future__ import annotations

import json

from superrobot.dr.framework_mapper import map_framework
from superrobot.dr.llm_gateway import LLMGateway, has_llm_credentials
from superrobot.models.analysis_result import AnalysisResult
from superrobot.models.scan_result import EntryPoint, ScanResult
from superrobot.pipeline.schema_inference import infer_schemas


async def analyze(
    scan_result: ScanResult,
    gateway: LLMGateway | None = None,
) -> AnalysisResult:
    """Analyze a ScanResult via LLM Gateway, with framework mapper fallback."""
    if gateway is None and not has_llm_credentials():
        return _fallback_analysis(scan_result)

    gw = gateway or LLMGateway()
    if not gw.available:
        return _fallback_analysis(scan_result)

    user_content = json.dumps(scan_result.model_dump(), indent=2)

    try:
        result = await gw.call("analyze", user_content, AnalysisResult)
        return result
    except Exception:
        return _fallback_analysis(scan_result)


def _fallback_analysis(scan: ScanResult) -> AnalysisResult:
    """Deterministic fallback when LLM is unavailable."""
    dr_framework, map_confidence = map_framework(
        scan.detected_framework,
        has_state_graph=scan.has_state_graph,
        has_workflow_yaml=scan.detected_framework == "nat",
    )
    # For frameworks without a first-class DR base class, keep the mapper's
    # medium confidence so the TUI asks the user to confirm — don't let a
    # high scan-detection score hide the mapping uncertainty.
    _needs_confirm = {
        "autogen",
        "semantic_kernel",
        "haystack",
        "openai_agents",
        "smolagents",
        "google_adk",
        "raw_async",
        "unknown",
    }
    if scan.detected_framework in _needs_confirm:
        confidence = map_confidence
        notes = (
            f"Detected {scan.detected_framework}; mapped to {dr_framework.value} "
            f"({map_confidence:.0%}). Confirm before generate."
        )
    else:
        confidence = min(map_confidence, scan.confidence)
        notes = "LLM unavailable; using deterministic scan + schema inference"
    entry = scan.primary_entry
    input_schema, output_schema = infer_schemas(scan.repo_path, entry)
    purpose = _purpose_from_scan(scan, entry)
    ui_components = _suggest_ui_components(input_schema, output_schema)
    return AnalysisResult(
        agent_purpose=purpose,
        dr_framework=dr_framework,
        input_schema=input_schema,
        output_schema=output_schema,
        suggested_ui_components=ui_components,
        missing_requirements=_missing_requirements(scan),
        risk_flags=[f.value for f in scan.risk_flags],
        notes=notes,
        confidence=confidence,
    )


def _purpose_from_scan(scan: ScanResult, entry: EntryPoint | None) -> str:
    if entry is not None:
        return f"{scan.detected_framework} agent with entry point {entry.function}()"
    return f"Agent detected as {scan.detected_framework} framework"


def _suggest_ui_components(
    input_schema: dict[str, str],
    output_schema: dict[str, str],
) -> list[str]:
    components = ["Card"]
    if any(t == "str" for t in input_schema.values()):
        components.insert(0, "TextInput")
    if len(input_schema) > 1:
        components.append("Form")
    if "confidence" in output_schema or len(output_schema) > 1:
        components.append("DataTable")
    return components


def _missing_requirements(scan: ScanResult) -> list[str]:
    missing: list[str] = []
    if "PROMPT_TEMPLATE_ID" not in scan.env_vars:
        missing.append("Prompt Management Registry entry (PROMPT_TEMPLATE_ID)")
    if not scan.entry_points:
        missing.append("No clear async entry point — confirm run_agent/process handler")
    if scan.detected_framework == "unknown":
        missing.append("Framework not recognized — confirm DR base class mapping")
    return missing
