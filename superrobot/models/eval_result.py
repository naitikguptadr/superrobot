"""Evaluation stage data models."""

from typing import Literal

from pydantic import BaseModel, Field


class EvalResult(BaseModel):
    """Result of a single eval run."""

    run_id: int
    input: str
    output: str | None = None
    status: Literal["pass", "fail", "error"]
    latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0
    failure_reason: str | None = None


class EvalSummary(BaseModel):
    """Aggregate eval results."""

    results: list[EvalResult] = Field(default_factory=list)
    passed: int = 0
    failed: int = 0
    errors: int = 0

    @property
    def total(self) -> int:
        return len(self.results)

    @classmethod
    def from_results(cls, results: list[EvalResult]) -> "EvalSummary":
        passed = sum(1 for r in results if r.status == "pass")
        failed = sum(1 for r in results if r.status == "fail")
        errors = sum(1 for r in results if r.status == "error")
        return cls(results=results, passed=passed, failed=failed, errors=errors)
