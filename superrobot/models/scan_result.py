"""Scan stage data models."""

from enum import StrEnum

from pydantic import BaseModel, Field


class RiskFlag(StrEnum):
    """Security or configuration risks detected during scan."""

    HARDCODED_SECRET = "hardcoded_secret"
    MISSING_ENV_EXAMPLE = "missing_env_example"
    NESTED_IMPORTS = "nested_imports"
    MISSING_DEPENDENCIES = "missing_dependencies"


class EntryPoint(BaseModel):
    """A discovered agent entry point."""

    file: str
    function: str
    signature: str = ""


class ScanResult(BaseModel):
    """Output of Stage 1 — static repo analysis."""

    detected_framework: str = Field(
        description=(
            "langchain | llamaindex | crewai | langgraph | pydantic_ai | raw_async | unknown"
        )
    )
    entry_points: list[EntryPoint] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)
    input_signatures: list[str] = Field(default_factory=list)
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    repo_path: str = ""
