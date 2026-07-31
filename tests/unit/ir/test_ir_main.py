"""The JSON transport the Pi harness calls.

Two contracts are pinned here, because the harness has no other way to tell
what happened:

* **stdout is always a single parseable JSON object**, on every path
  including failure. Audit C21 was exactly this bug -- an error path printed
  prose, so the shell reported "JSON parse failure" instead of the real
  problem, and the actual cause was invisible.
* **A refusal is not a crash.** `spec` on an unclean ledger carries
  `refusal: true`. If that field goes missing the harness agent treats the
  refusal as a transient failure and retries around a block it must not
  retry around, which defeats the whole architecture.

Everything runs in-process. `scaffold` is deliberately untested here: it
clones over the network and shells out to `dr`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from superrobot.ir.__main__ import main

CREWAI_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "crewai_agent"

AGENT_FACT = "llm_call:main.py:7:Agent"
CREW_FACT = "llm_call:main.py:9:Crew"

RESOLVING_DECISIONS = f"""
system_prompt: |
  You are a research crew.
examples:
  - Research LLM hallucination
facts:
  - fact: "{AGENT_FACT}"
    disposition: migrated
    reason: the crew uses the org default, which we are pinning to gpt-4o
    model: gpt-4o
  - fact: "{CREW_FACT}"
    disposition: migrated
    reason: same crew-level default
    model: gpt-4o
"""


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    """Invoke the transport and parse stdout, which must always be JSON."""
    code = main(list(argv))
    out = capsys.readouterr().out
    assert out.strip(), "stdout was empty; the harness has nothing to parse"
    payload = json.loads(out)
    assert isinstance(payload, dict), "the harness expects exactly one JSON object"
    return code, payload


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "crewai_agent"
    shutil.copytree(CREWAI_FIXTURE, repo)
    return repo


# --- extract -------------------------------------------------------------


def test_extract_returns_the_ir_and_its_coverage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, payload = _run(capsys, "extract", str(_repo(tmp_path)))

    assert code == 0
    assert payload["ir"]["name"] == "crewai_agent"
    assert payload["targetFramework"] == "crewai"
    coverage = payload["coverage"]
    assert set(coverage) == {"clean", "blocking", "unaccounted", "report"}
    assert coverage["clean"] is False
    assert any(AGENT_FACT in line for line in coverage["blocking"])
    assert coverage["unaccounted"] == []
    assert "Coverage:" in coverage["report"]


def test_extract_applies_a_decisions_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    decisions = tmp_path / "decisions.yaml"
    decisions.write_text(RESOLVING_DECISIONS)

    code, payload = _run(capsys, "extract", str(repo), "--decisions", str(decisions))

    assert code == 0
    assert payload["coverage"]["clean"] is True
    assert payload["coverage"]["blocking"] == []


# --- report --------------------------------------------------------------


def test_report_returns_a_report_and_a_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, payload = _run(capsys, "report", str(_repo(tmp_path)))

    assert code == 0
    assert set(payload) == {"report", "clean"}
    assert payload["clean"] is False
    assert AGENT_FACT in payload["report"]


def test_report_names_known_limits_that_are_not_blocking(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Warning-severity residue must reach the human too. It is the only
    record of what no probe looked for.
    """
    _, payload = _run(capsys, "report", str(_repo(tmp_path)))

    assert "KNOWN LIMITS" in payload["report"]


# --- decisions-template --------------------------------------------------


def test_decisions_template_returns_yaml_a_path_and_a_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)

    code, payload = _run(capsys, "decisions-template", str(repo))

    assert code == 0
    assert set(payload) == {"yaml", "path", "blockingCount"}
    assert payload["blockingCount"] == 2
    assert payload["path"] == str(repo / "superrobot-decisions.yaml")
    assert AGENT_FACT in payload["yaml"]
    assert not any(
        line.lstrip().startswith("disposition:") for line in payload["yaml"].splitlines()
    ), "the emitted template must not decide anything on its own"


# --- spec ----------------------------------------------------------------


def test_spec_on_a_clean_extraction_returns_parseable_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    decisions = tmp_path / "decisions.yaml"
    decisions.write_text(RESOLVING_DECISIONS)

    code, payload = _run(capsys, "spec", str(repo), "--decisions", str(decisions))

    assert code == 0
    spec = yaml.safe_load(payload["agentSpec"])
    assert spec["model"] == "gpt-4o"
    assert spec["system_prompt"] == "You are a research crew."


def test_spec_on_an_unclean_ledger_refuses_rather_than_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`refusal: true` is how the harness agent tells a deliberate block from
    a bug. Absent or false, it retries around a block that exists precisely
    to stop it -- which is the failure this architecture was built around.
    """
    code, payload = _run(capsys, "spec", str(_repo(tmp_path)))

    assert code == 1
    assert payload["refusal"] is True
    assert payload["blocking"], "a refusal with no blockers tells the human nothing"
    assert any(AGENT_FACT in line for line in payload["blocking"])
    assert payload["kind"] == "ProjectionError"


# --- error paths: stdout stays JSON --------------------------------------


def test_a_nonexistent_repo_fails_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A path that does not exist must not be analyzed as an empty agent:
    probing nothing finds nothing, and "found nothing" already means
    "the probes missed something", so a typo would be reported as a
    suspicious agent instead of as a typo.
    """
    code, payload = _run(capsys, "extract", str(tmp_path / "no_such_repo"))

    assert code == 1
    assert "no_such_repo" in payload["error"]
    assert payload["refusal"] is False


def test_a_malformed_decisions_file_fails_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    decisions = tmp_path / "decisions.yaml"
    decisions.write_text(f'facts:\n  - fact: "{AGENT_FACT}"\n    disposition: migrated\n')

    code, payload = _run(capsys, "extract", str(_repo(tmp_path)), "--decisions", str(decisions))

    assert code == 1
    assert payload["kind"] == "DecisionError"
    assert "no reason" in payload["error"]
    assert payload["refusal"] is False, "a malformed file is our caller's bug, not a refusal"


def test_a_missing_decisions_file_fails_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, payload = _run(
        capsys, "extract", str(_repo(tmp_path)), "--decisions", str(tmp_path / "typo.yaml")
    )

    assert code == 1
    assert payload["kind"] == "DecisionError"
    assert "typo.yaml" in payload["error"]


def test_error_payloads_never_leak_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Audit C21: prose on an error path made the shell report a JSON parse
    failure and hid the real cause.
    """
    _, payload = _run(capsys, "spec", str(tmp_path / "no_such_repo"))

    assert "Traceback" not in json.dumps(payload)
    assert set(payload) >= {"error", "kind", "refusal"}


# --- the loop the harness actually drives --------------------------------


def test_blocked_then_decided_then_specced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole point of the mechanism, end to end: a migration stops, a
    human answers exactly what stopped it, and only then does a spec exist.
    """
    repo = _repo(tmp_path)

    code, extracted = _run(capsys, "extract", str(repo))
    assert code == 0, "a blocked extraction is a finding, not a transport failure"
    assert extracted["coverage"]["clean"] is False

    code, template = _run(capsys, "decisions-template", str(repo))
    assert template["blockingCount"] == 2

    decisions = Path(template["path"])
    decisions.write_text(RESOLVING_DECISIONS)

    code, extracted = _run(capsys, "extract", str(repo), "--decisions", str(decisions))
    assert code == 0
    assert extracted["coverage"]["clean"] is True

    code, spec = _run(capsys, "spec", str(repo), "--decisions", str(decisions))
    assert code == 0
    assert yaml.safe_load(spec["agentSpec"])["model"] == "gpt-4o"
