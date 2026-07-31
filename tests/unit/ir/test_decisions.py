"""Human decisions -- the one sanctioned way to unblock a migration.

Every other path in this architecture refuses to guess. This file is the
exception, which makes it the place the governing invariant ("nothing is
silently dropped") is most at risk. The tests below are what keep the
exception honest: a decision must name a real fact, must carry a reason,
cannot invent a block, and cannot delete residue.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from superrobot.ir.decisions import (
    DecisionError,
    Decisions,
    FactDecision,
    load_decisions,
    parse_decisions,
    render_decisions_template,
)
from superrobot.ir.extract import extract_migration_ir
from superrobot.ir.model import Disposition, Severity

CREWAI_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "crewai_agent"

# The two implicit-default-model call sites the crewai fixture blocks on.
# `test_the_fixture_blocks_on_two_implicit_model_facts` pins these, so a
# probe change that renames them fails there rather than everywhere.
AGENT_FACT = "llm_call:main.py:7:Agent"
CREW_FACT = "llm_call:main.py:9:Crew"


def _decided(fact: str, **overrides: str) -> str:
    entry = {"disposition": "migrated", "reason": "the org default is gpt-4o", **overrides}
    lines = [f'  - fact: "{fact}"'] + [f"    {k}: {v}" for k, v in entry.items()]
    return "\n".join(lines)


def _both_facts_decided(**overrides: str) -> str:
    return "facts:\n" + "\n".join(_decided(f, **overrides) for f in (AGENT_FACT, CREW_FACT))


# --- parsing -------------------------------------------------------------


def test_a_well_formed_document_parses_every_field() -> None:
    decisions = parse_decisions(
        """
model: azure/gpt-4o
system_prompt: |
  You are a research assistant.
examples:
  - Find recent papers on LLM hallucination
target_framework: crewai
frontend:
  type: custom
facts:
  - fact: "llm_call:main.py:7:Agent"
    disposition: migrated
    reason: the crew uses the org default
    model: gpt-4o
acknowledged_residue:
  - no LLM call site was found in this repo
"""
    )

    assert decisions.model == "azure/gpt-4o"
    assert decisions.system_prompt.strip() == "You are a research assistant."  # type: ignore[union-attr]
    assert decisions.examples == ["Find recent papers on LLM hallucination"]
    assert decisions.target_framework == "crewai"
    assert decisions.frontend.type == "custom"
    assert decisions.acknowledges("no LLM call site was found in this repo")

    decision = decisions.for_fact(AGENT_FACT)
    assert decision == FactDecision(
        fact=AGENT_FACT,
        disposition=Disposition.MIGRATED,
        reason="the crew uses the org default",
        model="gpt-4o",
    )


def test_an_empty_document_parses_to_empty_decisions() -> None:
    decisions = parse_decisions("")

    assert decisions.facts == {}
    assert decisions.acknowledged_residue == []
    assert decisions.for_fact(AGENT_FACT) is None


@pytest.mark.parametrize("disposition", ["migrated", "deferred"])
def test_a_decision_with_no_reason_is_rejected(disposition: str) -> None:
    """The single thing this file exists to prevent. Overriding a block
    without saying why is exactly how a gap disappears quietly, and it is no
    more acceptable for `migrated` than for `deferred`.
    """
    text = f'facts:\n  - fact: "{AGENT_FACT}"\n    disposition: {disposition}\n'

    with pytest.raises(DecisionError, match="no reason"):
        parse_decisions(text)


def test_a_whitespace_only_reason_is_rejected() -> None:
    """A reason of `"   "` satisfies "the key is present" and explains
    nothing; treating it as given would make the check cosmetic.
    """
    text = f'facts:\n  - fact: "{AGENT_FACT}"\n    disposition: migrated\n    reason: "   "\n'

    with pytest.raises(DecisionError, match="no reason"):
        parse_decisions(text)


def test_a_decision_cannot_set_blocking() -> None:
    """A human decision resolves a block; it does not create one. Allowing
    `blocking` here would make the decisions file a second, unaudited source
    of blockers competing with the probes.
    """
    text = (
        f'facts:\n  - fact: "{AGENT_FACT}"\n    disposition: blocking\n'
        "    reason: I would rather this stayed blocked\n"
    )

    with pytest.raises(DecisionError, match="does not create one"):
        parse_decisions(text)


def test_an_unknown_disposition_names_the_valid_ones() -> None:
    text = f'facts:\n  - fact: "{AGENT_FACT}"\n    disposition: probably_fine\n    reason: eh\n'

    with pytest.raises(DecisionError) as excinfo:
        parse_decisions(text)

    message = str(excinfo.value)
    assert "probably_fine" in message
    assert "migrated" in message and "deferred" in message


def test_the_same_fact_decided_twice_is_rejected() -> None:
    """The second entry would win and the first would vanish -- a silent
    drop inside the very file that exists to stop silent drops.
    """
    text = "facts:\n" + _decided(AGENT_FACT) + "\n" + _decided(AGENT_FACT, disposition="deferred")

    with pytest.raises(DecisionError, match="decided twice"):
        parse_decisions(text)


def test_a_facts_entry_with_no_fact_id_is_rejected() -> None:
    with pytest.raises(DecisionError, match="needs a `fact` id"):
        parse_decisions("facts:\n  - disposition: migrated\n    reason: because\n")


def test_non_yaml_is_a_decision_error_not_a_yaml_error() -> None:
    """The harness reports `DecisionError` as a human-fixable problem. A raw
    `yaml.YAMLError` escaping here would surface as a crash instead.
    """
    with pytest.raises(DecisionError, match="not valid YAML"):
        parse_decisions("facts: [unclosed\n  - : : :\n")


@pytest.mark.parametrize("text", ["just a string", "- a\n- b\n", "42"])
def test_yaml_that_is_not_a_mapping_is_rejected(text: str) -> None:
    """Without this the code would `.get()` a list or a str and raise
    `AttributeError`, which reads as a bug in us rather than in the file.
    """
    with pytest.raises(DecisionError, match="mapping"):
        parse_decisions(text)


# --- loading -------------------------------------------------------------


def test_loading_no_path_yields_empty_decisions() -> None:
    """No decisions file is a legitimate state: every gap simply blocks."""
    assert load_decisions(None) == Decisions()


def test_loading_a_missing_path_raises(tmp_path: Path) -> None:
    """Proceeding without decisions would make a typo'd path look like an
    unresolved migration, and the human would re-answer questions they
    already answered.
    """
    with pytest.raises(DecisionError, match="not found"):
        load_decisions(tmp_path / "typo.yaml")


def test_loading_reads_the_file(tmp_path: Path) -> None:
    path = tmp_path / "superrobot-decisions.yaml"
    path.write_text("facts:\n" + _decided(AGENT_FACT))

    assert load_decisions(path).for_fact(AGENT_FACT) is not None


# --- template ------------------------------------------------------------


def _template(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "blocking": [
            (AGENT_FACT, "llm_call", "relies on a default model"),
            (CREW_FACT, "tool", ""),
        ],
        "needs_model": True,
        "needs_system_prompt": True,
        "suggested_framework": "crewai",
    }
    kwargs.update(overrides)
    return render_decisions_template(
        kwargs.pop("blocking"),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def test_the_template_lists_every_blocking_fact() -> None:
    text = _template()

    assert AGENT_FACT in text
    assert CREW_FACT in text
    assert "relies on a default model" in text
    assert "2 blocking fact(s)" in text


def test_the_template_is_entirely_commented_out() -> None:
    """The template is generated unattended. If any decision line were live,
    a run that never reached a human would silently acknowledge a blocker --
    the exact failure the decisions mechanism exists to prevent.
    """
    text = _template()

    live = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and line.strip() != "facts:"
    ]
    assert live == [], f"template contains uncommented content: {live}"
    assert not any(line.lstrip().startswith("disposition:") for line in text.splitlines()), (
        "an uncommented disposition would be applied without anyone deciding it"
    )
    assert not any(line.lstrip().startswith("acknowledged_residue:") for line in text.splitlines())


def test_the_template_offers_a_model_only_when_one_is_needed() -> None:
    assert "# model: azure/gpt-4o" in _template(needs_model=True)
    assert "# model: azure/gpt-4o" not in _template(needs_model=False)


def test_the_template_offers_a_system_prompt_only_when_one_is_needed() -> None:
    assert "# system_prompt:" in _template(needs_system_prompt=True)
    assert "# system_prompt:" not in _template(needs_system_prompt=False)


def test_the_template_says_so_when_nothing_needs_deciding() -> None:
    text = _template(blocking=[])

    assert "No blocking facts" in text
    assert "facts: []" in text


# --- applied during extraction -------------------------------------------


def test_the_fixture_blocks_on_two_implicit_default_model_facts() -> None:
    """The premise of every integration test below. If the crewai fixture
    stops blocking, those tests would pass without proving anything.
    """
    extraction = extract_migration_ir(CREWAI_FIXTURE)

    assert not extraction.is_clean()
    assert sorted(f.id for f in extraction.ledger.blocking()) == sorted([AGENT_FACT, CREW_FACT])


def test_deciding_every_blocker_makes_the_extraction_clean() -> None:
    decisions = parse_decisions(_both_facts_decided(model="gpt-4o"))

    extraction = extract_migration_ir(CREWAI_FIXTURE, decisions=decisions)

    assert extraction.ledger.blocking() == []
    assert extraction.is_clean()


def test_a_deferral_also_resolves_a_blocker() -> None:
    """Deferring is a recorded decision, not a gap -- the ledger treats it
    as clean, and the report still names it.
    """
    decisions = parse_decisions(_both_facts_decided(disposition="deferred"))

    extraction = extract_migration_ir(CREWAI_FIXTURE, decisions=decisions)

    assert extraction.is_clean()
    assert len(extraction.ledger.deferred()) == 2


def test_the_report_marks_a_reason_as_a_human_decision() -> None:
    """`report()` must distinguish what we derived from what someone
    asserted; an unlabelled reason reads as a probe's own finding.
    """
    decisions = parse_decisions(_both_facts_decided(disposition="deferred"))

    report = extract_migration_ir(CREWAI_FIXTURE, decisions=decisions).ledger.report()

    assert "human decision: the org default is gpt-4o" in report


def test_a_per_fact_model_fills_the_model_the_probe_could_not_resolve() -> None:
    decisions = parse_decisions(_both_facts_decided(model="gpt-4o"))

    extraction = extract_migration_ir(CREWAI_FIXTURE, decisions=decisions)

    assert [c.model for c in extraction.ir.llm_calls] == ["gpt-4o", "gpt-4o"]


def test_a_top_level_model_fills_call_sites_that_named_none() -> None:
    decisions = parse_decisions("model: azure/gpt-4o\n" + _both_facts_decided())

    extraction = extract_migration_ir(CREWAI_FIXTURE, decisions=decisions)

    assert [c.model for c in extraction.ir.llm_calls] == ["azure/gpt-4o", "azure/gpt-4o"]


def test_a_decision_naming_an_unknown_fact_blocks_rather_than_no_ops() -> None:
    """The stale-decisions-file case. A decision that matches nothing must
    not quietly under-apply: the file has drifted from the code, and the run
    that follows would block for reasons the human thought they had already
    answered.
    """
    decisions = parse_decisions(_both_facts_decided() + "\n" + _decided("llm_call:gone.py:1:Agent"))

    extraction = extract_migration_ir(CREWAI_FIXTURE, decisions=decisions)

    stale = [r for r in extraction.ir.residue if "matched no source fact" in r.description]
    assert len(stale) == 1
    assert "llm_call:gone.py:1:Agent" in stale[0].description
    assert stale[0].severity is Severity.BLOCKING
    assert not extraction.is_clean(), "a drifted decisions file must stop the migration"


def test_acknowledging_residue_downgrades_it_without_removing_it(tmp_path: Path) -> None:
    """The gap was real when we found it and is still real after someone
    accepted it. What acknowledgement changes is whether it stops the
    migration -- never whether it appears in the report.
    """
    (tmp_path / "main.py").write_text("def run():\n    return 1\n")
    description = "no LLM call site was found in this repo"
    decisions = parse_decisions(f"acknowledged_residue:\n  - {description}\n")

    extraction = extract_migration_ir(tmp_path, decisions=decisions)

    (entry,) = [r for r in extraction.ir.residue if r.description == description]
    assert entry.severity is Severity.WARNING
    assert "acknowledged by a human decision" in entry.reason


def test_unacknowledged_residue_still_blocks(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def run():\n    return 1\n")

    extraction = extract_migration_ir(tmp_path)

    (entry,) = [r for r in extraction.ir.residue if "no LLM call site" in r.description]
    assert entry.severity is Severity.BLOCKING
    assert not extraction.is_clean()


def test_decisions_carry_the_spec_fields_no_probe_can_extract(tmp_path: Path) -> None:
    """System prompt, examples and target framework have no source to be
    read from; the decisions file is the only way they reach the IR.
    """
    repo = tmp_path / "crewai_agent"
    shutil.copytree(CREWAI_FIXTURE, repo)
    decisions = parse_decisions(
        "system_prompt: You are a research crew.\n"
        "examples:\n  - Research LLM hallucination\n"
        "target_framework: base\n" + _both_facts_decided(model="gpt-4o")
    )

    extraction = extract_migration_ir(repo, decisions=decisions)

    assert extraction.ir.system_prompt == "You are a research crew."
    assert extraction.ir.examples == ["Research LLM hallucination"]
    assert extraction.ir.target_framework == "base", "a decision overrides the derived framework"
