"""Schema invariants of the Migration IR."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from superrobot.ir.model import Evidence, LlmCall, MigrationIR, Tool


def _evidence() -> Evidence:
    return Evidence(file="main.py", line=1, node_id="main::llm")


def test_an_element_without_evidence_is_rejected() -> None:
    """A claim with no file:line behind it cannot be checked against the
    CPG, which is the only thing standing between an LLM's assertion and
    the generated agent.
    """
    with pytest.raises(ValidationError):
        Tool(name="search", callable="tools.search", evidence=[])


def test_an_element_with_evidence_is_accepted() -> None:
    tool = Tool(name="search", callable="tools.search", evidence=[_evidence()])

    assert str(tool.evidence[0]) == "main.py:1"


def test_an_unresolved_model_is_kept_rather_than_dropped() -> None:
    """The dataflow probe could not resolve it; the IR must still carry the
    expression so the ledger has something to block on.
    """
    call = LlmCall(
        client="ChatOpenAI",
        model=None,
        unresolved_model=['os.environ["MODEL"]'],
        evidence=[_evidence()],
    )

    assert call.model is None
    assert call.unresolved_model == ['os.environ["MODEL"]']


def test_primary_model_skips_calls_whose_model_is_unresolved() -> None:
    ir = MigrationIR(
        source_repo="/repo",
        name="demo",
        llm_calls=[
            LlmCall(client="ChatOpenAI", model=None, evidence=[_evidence()]),
            LlmCall(client="ChatAnthropic", model="claude-opus-4-8", evidence=[_evidence()]),
        ],
    )

    assert ir.primary_model() == "claude-opus-4-8"


def test_primary_model_is_none_when_nothing_resolved() -> None:
    ir = MigrationIR(
        source_repo="/repo",
        name="demo",
        llm_calls=[LlmCall(client="ChatOpenAI", model=None, evidence=[_evidence()])],
    )

    assert ir.primary_model() is None
