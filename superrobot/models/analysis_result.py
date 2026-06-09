"""Analyze stage data models."""

from enum import StrEnum

from pydantic import BaseModel, Field


class DrFramework(StrEnum):
    """DataRobot agent framework targets."""

    LANGGRAPH = "langgraph"
    CREWAI = "crewai"
    LLAMAINDEX = "llamaindex"
    NAT = "nat"
    PYDANTIC_AI = "pydantic_ai"


class AnalysisResult(BaseModel):
    """Output of Stage 2 — LLM analysis of ScanResult."""

    agent_purpose: str = ""
    dr_framework: DrFramework = DrFramework.LANGGRAPH
    input_schema: dict[str, str] = Field(default_factory=dict)
    output_schema: dict[str, str] = Field(default_factory=dict)
    suggested_ui_components: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    notes: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
