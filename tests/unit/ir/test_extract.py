"""Assembling a Migration IR from the deterministic probes.

Phase 1 wires only the LLM call-site probe and entry-point resolution. What
matters here is not breadth but that the ledger's invariant survives the
assembly: every fact the probes enumerated gets a disposition, and the
cases we cannot honestly migrate block instead of passing.
"""

from __future__ import annotations

from pathlib import Path

from superrobot.ir.extract import extract_migration_ir


def _repo(tmp_path: Path, **files: str) -> Path:
    for name, source in files.items():
        path = tmp_path / f"{name}.py"
        path.write_text(source)
    return tmp_path


def test_a_resolvable_known_client_migrates_cleanly(tmp_path: Path) -> None:
    repo = _repo(tmp_path, main='llm = ChatOpenAI(model="gpt-4o")\n')

    extraction = extract_migration_ir(repo)

    assert [c.model for c in extraction.ir.llm_calls] == ["gpt-4o"]
    assert extraction.ledger.is_clean()


def test_every_llm_call_becomes_a_fact_with_provenance(tmp_path: Path) -> None:
    repo = _repo(tmp_path, main='llm = ChatOpenAI(model="gpt-4o")\n')

    extraction = extract_migration_ir(repo)

    (call,) = extraction.ir.llm_calls
    assert call.evidence, "an IR element with no evidence cannot be checked"
    assert call.evidence[0].file.endswith("main.py")
    assert call.evidence[0].line == 1
    assert call.fact_id, "the element must link back to the fact it accounts for"


def test_an_unknown_provider_blocks_rather_than_passing(tmp_path: Path) -> None:
    """The governing invariant, end to end: we have no shim for
    ChatFireworks, so the migration stops instead of shipping an agent
    quietly missing a model call.
    """
    repo = _repo(tmp_path, main='llm = ChatFireworks(model="llama-v3")\n')

    extraction = extract_migration_ir(repo)

    assert not extraction.ledger.is_clean()
    assert len(extraction.ledger.blocking()) == 1
    assert "ChatFireworks" in extraction.ledger.report()


def test_an_unresolvable_model_blocks(tmp_path: Path) -> None:
    repo = _repo(tmp_path, main='import os\nllm = ChatOpenAI(model=os.environ["MODEL"])\n')

    extraction = extract_migration_ir(repo)

    assert not extraction.ledger.is_clean()
    assert extraction.ledger.blocking()
    assert "MODEL" in extraction.ledger.report()
    (call,) = extraction.ir.llm_calls
    assert call.model is None
    assert call.unresolved_model, "the expression that defeated us must survive into the IR"


def test_no_fact_is_left_unaccounted(tmp_path: Path) -> None:
    """Whatever the mix, the extractor must disposition everything it found."""
    repo = _repo(
        tmp_path,
        a='llm = ChatOpenAI(model="gpt-4o")\n',
        b='llm = ChatFireworks(model="llama-v3")\n',
        c='import os\nllm = ChatAnthropic(model=os.environ["M"])\n',
    )

    extraction = extract_migration_ir(repo)

    assert extraction.ledger.unaccounted() == []


def test_the_coverage_snapshot_is_embedded_in_the_ir(tmp_path: Path) -> None:
    repo = _repo(tmp_path, main='llm = ChatOpenAI(model="gpt-4o")\n')

    extraction = extract_migration_ir(repo)

    assert extraction.ir.coverage is not None
    assert extraction.ir.coverage.is_clean()


def test_phase_one_records_what_it_does_not_yet_extract(tmp_path: Path) -> None:
    """No tool, state, or orchestration probe exists yet. That hole must
    travel with the artifact rather than read as 'this agent has no tools'.
    """
    repo = _repo(tmp_path, main='llm = ChatOpenAI(model="gpt-4o")\n')

    extraction = extract_migration_ir(repo)

    assert extraction.ir.residue, "an unimplemented probe is a known gap, not an absence"
    assert any("tool" in r.description.lower() for r in extraction.ir.residue)


def test_a_repo_with_no_llm_call_is_not_silently_fine(tmp_path: Path) -> None:
    """An agent repo with zero detected call sites is far more likely to
    mean the probe missed something than that the agent talks to no model.
    """
    repo = _repo(tmp_path, main="def run():\n    return 1\n")

    extraction = extract_migration_ir(repo)

    assert extraction.ir.llm_calls == []
    assert any("no LLM call site" in r.description for r in extraction.ir.residue)


def test_a_zero_finding_repo_reconciles_but_is_still_not_clean(tmp_path: Path) -> None:
    """The hole this guards: a ledger over zero facts reconciles perfectly.
    Three of our own fixtures do exactly that. The verdict that gates the
    pipeline must count blocking residue, not just fact dispositions.
    """
    repo = _repo(tmp_path, main="def run():\n    return 1\n")

    extraction = extract_migration_ir(repo)

    assert extraction.ledger.is_clean(), "nothing was found, so nothing is unaccounted"
    assert not extraction.is_clean(), "but finding nothing is itself the blocker"
