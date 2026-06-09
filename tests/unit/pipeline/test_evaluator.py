"""Evaluator unit tests with mocked dr CLI."""

import pytest
from pytest_mock import MockerFixture

from superrobot.dr.cli_wrapper import DrCommandResult
from superrobot.models.analysis_result import AnalysisResult
from superrobot.pipeline.evaluator import _evaluate_output, _generate_inputs, run_eval


@pytest.fixture
def analysis() -> AnalysisResult:
    return AnalysisResult(
        agent_purpose="Test agent",
        input_schema={"query": "str"},
        output_schema={"response": "str"},
        confidence=0.9,
    )


def test_generate_inputs_count(analysis: AnalysisResult) -> None:
    inputs = _generate_inputs(analysis)
    assert len(inputs) == 5
    assert all("query" in inp for inp in inputs)


def test_evaluate_output_pass(analysis: AnalysisResult) -> None:
    status, reason = _evaluate_output('{"response": "ok"}', analysis, 100.0)
    assert status == "pass"
    assert reason is None


def test_evaluate_output_schema_violation(analysis: AnalysisResult) -> None:
    status, reason = _evaluate_output('{"wrong": "field"}', analysis, 100.0)
    assert status == "fail"
    assert reason == "schema_violation"


def test_evaluate_output_timeout(analysis: AnalysisResult) -> None:
    status, reason = _evaluate_output('{"response": "ok"}', analysis, 35_000.0)
    assert status == "fail"
    assert reason == "timeout"


@pytest.mark.asyncio
async def test_run_eval_with_mock_cli(analysis: AnalysisResult, mocker: MockerFixture) -> None:
    mock_cli = mocker.Mock()
    mock_cli.run_dev = mocker.AsyncMock(
        return_value=DrCommandResult(returncode=0, stdout='{"response": "ok"}', stderr="")
    )
    summary = await run_eval(analysis, cli=mock_cli)
    assert summary.total == 5
    assert summary.passed == 5
