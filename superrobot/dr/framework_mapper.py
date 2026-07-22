"""Foreign framework detection → DR framework mapping."""

from __future__ import annotations

from superrobot.models.analysis_result import DrFramework

# Frameworks with no first-class DR base class map to LangGraph as the
# closest orchestration target. Confidence stays medium so the TUI asks.
_LANGGRAPH_FALLBACKS: dict[str, float] = {
    "autogen": 0.55,
    "semantic_kernel": 0.55,
    "haystack": 0.5,
    "openai_agents": 0.55,
    "smolagents": 0.5,
    "google_adk": 0.5,
    "raw_async": 0.4,
    "unknown": 0.3,
}


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
    }

    if has_workflow_yaml:
        return DrFramework.NAT, 0.95

    if detected == "langchain":
        return DrFramework.LANGGRAPH, 0.9 if has_state_graph else 0.7

    if detected in mapping:
        return mapping[detected]

    if detected in _LANGGRAPH_FALLBACKS:
        return DrFramework.LANGGRAPH, _LANGGRAPH_FALLBACKS[detected]

    return DrFramework.LANGGRAPH, 0.3


def supported_foreign_frameworks() -> list[str]:
    """Foreign frameworks the scanner can detect (for docs / confirm UI)."""
    return sorted(
        {
            "langchain",
            "langgraph",
            "crewai",
            "llamaindex",
            "pydantic_ai",
            "nat",
            "autogen",
            "semantic_kernel",
            "haystack",
            "openai_agents",
            "smolagents",
            "google_adk",
            "raw_async",
        }
    )


def dr_framework_choices() -> list[str]:
    """DR target frameworks the user can confirm in the TUI."""
    return [f.value for f in DrFramework]
