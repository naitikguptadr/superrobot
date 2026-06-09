"""Pre-deploy 5-shot evaluation — Stage 5."""

from __future__ import annotations

import json
import time
from typing import Literal, cast

from superrobot.dr.cli_wrapper import DrCliWrapper
from superrobot.models.analysis_result import AnalysisResult
from superrobot.models.eval_result import EvalResult, EvalSummary

EvalStatus = Literal["pass", "fail", "error"]

EVAL_RUN_COUNT = 5
LATENCY_LIMIT_MS = 30_000.0


async def run_eval(
    analysis: AnalysisResult,
    cwd: str | None = None,
    cli: DrCliWrapper | None = None,
) -> EvalSummary:
    """Run 5-shot eval against locally-running agent via dr run dev."""
    wrapper = cli or DrCliWrapper()
    inputs = _generate_inputs(analysis)
    results: list[EvalResult] = []

    for i, inp in enumerate(inputs, 1):
        start = time.perf_counter()
        result = await wrapper.run_dev(json.dumps(inp), cwd=cwd)
        latency_ms = (time.perf_counter() - start) * 1000

        if result.returncode == 124:
            results.append(
                EvalResult(
                    run_id=i,
                    input=json.dumps(inp),
                    status="error",
                    latency_ms=latency_ms,
                    failure_reason="timeout",
                )
            )
            continue

        if not result.ok:
            results.append(
                EvalResult(
                    run_id=i,
                    input=json.dumps(inp),
                    status="error",
                    latency_ms=latency_ms,
                    failure_reason="crash",
                )
            )
            continue

        output = result.stdout.strip() or None
        status, reason = _evaluate_output(output, analysis, latency_ms)
        results.append(
            EvalResult(
                run_id=i,
                input=json.dumps(inp),
                output=output,
                status=cast(EvalStatus, status),
                latency_ms=latency_ms,
                estimated_cost_usd=0.004,
                failure_reason=reason,
            )
        )

    return EvalSummary.from_results(results)


def _generate_inputs(analysis: AnalysisResult) -> list[dict[str, str]]:
    """Generate synthetic inputs from agent purpose and input schema."""
    base_query = f"Test query for: {analysis.agent_purpose}"
    inputs: list[dict[str, str]] = []
    for i in range(EVAL_RUN_COUNT):
        inp: dict[str, str] = {}
        if analysis.input_schema:
            for key, typ in analysis.input_schema.items():
                inp[key] = f"{base_query} ({i + 1})" if typ == "str" else str(i + 1)
        else:
            inp = {"query": f"{base_query} ({i + 1})"}
        inputs.append(inp)
    return inputs


def _evaluate_output(
    output: str | None,
    analysis: AnalysisResult,
    latency_ms: float,
) -> tuple[str, str | None]:
    if output is None:
        return "fail", "crash"
    if latency_ms > LATENCY_LIMIT_MS:
        return "fail", "timeout"
    if not analysis.output_schema:
        return "pass", None
    try:
        data = json.loads(output)
        for key in analysis.output_schema:
            if key not in data:
                return "fail", "schema_violation"
    except json.JSONDecodeError:
        if analysis.output_schema:
            return "fail", "schema_violation"
    return "pass", None
