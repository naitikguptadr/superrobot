"""Foreign framework detection → DR framework mapping."""

from __future__ import annotations

from superrobot.models.analysis_result import DrFramework


def map_framework(
    detected: str,
    has_state_graph: bool = False,
    has_workflow_yaml: bool = False,
) -> tuple[DrFramework, float]:
    """Map detected foreign framework to DR framework with confidence."""
    mapping: dict[str, tuple[DrFramework, float]] = {
        "langgraph": (DrFramework.LANGGRAPH, 0.95),
        "crewai": (DrFramework.CREWAI, 0.95),
        "llamaindex": (DrFramework.LLAMAINDEX, 0.95),
        "pydantic_ai": (DrFramework.PYDANTIC_AI, 0.95),
        "raw_async": (DrFramework.LANGGRAPH, 0.4),
        "unknown": (DrFramework.LANGGRAPH, 0.3),
    }

    if has_workflow_yaml:
        return DrFramework.NAT, 0.95

    if detected == "langchain":
        return DrFramework.LANGGRAPH, 0.9 if has_state_graph else 0.7

    return mapping.get(detected, (DrFramework.LANGGRAPH, 0.3))
