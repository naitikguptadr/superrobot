"""Evaluator tests — direct-execution fallback."""

from __future__ import annotations

import asyncio
from pathlib import Path

from superrobot.models.analysis_result import AnalysisResult, DrFramework
from superrobot.pipeline.evaluator import _crash_reason, run_eval


class _NoDrCli:
    """Simulates a machine where dr run dev always fails."""

    async def run_dev(self, input_json: str, cwd: str | None = None, timeout: float = 30.0):
        from superrobot.dr.cli_wrapper import DrCommandResult

        return DrCommandResult(returncode=1, stdout="", stderr="unknown command")


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        agent_purpose="echo",
        dr_framework=DrFramework.LANGGRAPH,
        input_schema={"query": "str"},
        output_schema={"response": "str"},
        suggested_ui_components=[],
        confidence=1.0,
    )


def test_direct_fallback_runs_migrated_logic(tmp_path: Path) -> None:
    bundle = tmp_path / "agent" / "agent"
    bundle.mkdir(parents=True)
    (bundle / "main.py").write_text(
        "async def run_agent(query):\n    return {'response': f'ok: {query}'}\n"
    )
    summary = asyncio.run(
        run_eval(
            _analysis(),
            cwd=str(tmp_path),
            cli=_NoDrCli(),  # type: ignore[arg-type]
            entry=("main", "run_agent", ["query"]),
        )
    )
    assert summary.passed == summary.total == 5
    assert summary.results[0].output is not None
    assert "ok:" in summary.results[0].output


def test_direct_fallback_reports_real_failure_reason(tmp_path: Path) -> None:
    bundle = tmp_path / "agent" / "agent"
    bundle.mkdir(parents=True)
    (bundle / "main.py").write_text("import not_a_real_module\n")
    summary = asyncio.run(
        run_eval(
            _analysis(),
            cwd=str(tmp_path),
            cli=_NoDrCli(),  # type: ignore[arg-type]
            entry=("main", "run_agent", ["query"]),
        )
    )
    assert summary.errors == 5
    reason = summary.results[0].failure_reason or ""
    assert "ModuleNotFoundError" in reason


def test_no_entry_keeps_crash_status() -> None:
    summary = asyncio.run(
        run_eval(_analysis(), cwd=".", cli=_NoDrCli(), entry=None)  # type: ignore[arg-type]
    )
    assert summary.errors == 5


def test_crash_reason_distils_stderr() -> None:
    assert _crash_reason("Traceback...\nValueError: bad input") == "crash: ValueError: bad input"
    assert _crash_reason("") == "crash"
