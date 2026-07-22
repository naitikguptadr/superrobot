"""Gap Analysis finding/report models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

GapSeverity = Literal["blocking", "warning"]


class GapFinding(BaseModel):
    """A single platform-rule finding against a generated package."""

    rule: str
    severity: GapSeverity
    message: str
    file: str | None = None


class GapReport(BaseModel):
    """Aggregate Gap Analysis findings for a generated package."""

    findings: list[GapFinding] = Field(default_factory=list)

    @property
    def blocking(self) -> list[GapFinding]:
        return [f for f in self.findings if f.severity == "blocking"]

    @property
    def warnings(self) -> list[GapFinding]:
        return [f for f in self.findings if f.severity == "warning"]
