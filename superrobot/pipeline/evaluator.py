"""Pre-deploy 5-shot evaluation — Stage 5."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Literal, cast

from superrobot.dr.cli_wrapper import DrCliWrapper, DrCommandResult
from superrobot.models.analysis_result import AnalysisResult
from superrobot.models.eval_result import EvalResult, EvalSummary

EvalStatus = Literal["pass", "fail", "error"]

EVAL_RUN_COUNT = 5
LATENCY_LIMIT_MS = 30_000.0

# Executed with python -c to run the migrated entry point directly when
# `dr run dev` is unavailable. argv: [json_payload, module, function, params_json]
_DIRECT_RUNNER = """
import asyncio, inspect, json, sys
sys.path.insert(0, "agent/agent")
sys.path.insert(0, ".")
payload = json.loads(sys.argv[1])
module = __import__(sys.argv[2])
fn = getattr(module, sys.argv[3])
params = json.loads(sys.argv[4])
kwargs = {k: v for k, v in payload.items() if k in params}
result = fn(**kwargs) if kwargs else fn(payload)
if inspect.isawaitable(result):
    result = asyncio.run(result)
if not isinstance(result, (dict, list)):
    result = {"response": str(result)}
print(json.dumps(result))
"""


def _eval_python(cwd: str | None) -> str:
    """Interpreter for direct execution — prefers the agent's own environment.

    Order: SUPERROBOT_EVAL_PYTHON env var, the source repo's .venv (when cwd is
    the generated <repo>/.superrobot bundle), then SuperRobot's interpreter.
    """
    override = os.environ.get("SUPERROBOT_EVAL_PYTHON", "").strip()
    if override:
        return override
    if cwd:
        repo_venv = Path(cwd).resolve().parent / ".venv" / "bin" / "python"
        if repo_venv.exists():
            return str(repo_venv)
    return sys.executable


async def _run_direct(
    inp: dict[str, str],
    entry: tuple[str, str, list[str]],
    cwd: str | None,
) -> DrCommandResult:
    """Execute the migrated entry point in a subprocess (dr-run-dev fallback)."""
    module, function, params = entry
    try:
        proc = await asyncio.create_subprocess_exec(
            _eval_python(cwd),
            "-c",
            _DIRECT_RUNNER,
            json.dumps(inp),
            module,
            function,
            json.dumps(params),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env={**os.environ, "PYTHONPATH": str(Path(cwd or ".") / "agent" / "agent")},
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=LATENCY_LIMIT_MS / 1000
        )
        return DrCommandResult(
            returncode=proc.returncode or 0,
            stdout=stdout_b.decode(),
            stderr=stderr_b.decode(),
        )
    except TimeoutError:
        return DrCommandResult(returncode=124, stdout="", stderr="timeout")


def _crash_reason(stderr: str) -> str:
    """Distil a subprocess stderr into a one-line, human-readable reason."""
    lines = [line.strip() for line in stderr.strip().splitlines() if line.strip()]
    if not lines:
        return "crash"
    return f"crash: {lines[-1][:120]}"


async def run_eval(
    analysis: AnalysisResult,
    cwd: str | None = None,
    cli: DrCliWrapper | None = None,
    entry: tuple[str, str, list[str]] | None = None,
) -> EvalSummary:
    """Run 5-shot eval via dr run dev, falling back to direct execution.

    `entry` is (flat_module, function, param_names) of the migrated logic —
    when provided, a failed `dr run dev` falls back to running the agent's
    entry point directly in a subprocess so the eval still exercises real code.
    """
    wrapper = cli or DrCliWrapper()
    inputs = _generate_inputs(analysis)
    results: list[EvalResult] = []

    for i, inp in enumerate(inputs, 1):
        start = time.perf_counter()
        result = await wrapper.run_dev(json.dumps(inp), cwd=cwd)
        if not result.ok and entry is not None:
            result = await _run_direct(inp, entry, cwd)
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
                    failure_reason=_crash_reason(result.stderr),
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
