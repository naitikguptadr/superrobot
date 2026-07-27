"""Tests for graph-based enrichment of an existing ScanResult."""

from __future__ import annotations

from pathlib import Path

import pytest

from superrobot.pipeline.graph.builder import GraphBuildTimeout
from superrobot.pipeline.graph.enrich import enrich_scan_result
from superrobot.pipeline.graph.framework_detect import FrameworkDetection
from superrobot.pipeline.scanner import scan


def test_enrichment_never_lowers_scanner_confidence(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from crewai import Agent\n\ndef run_agent():\n    return Agent\n"
    )
    base = scan(tmp_path)
    enriched = enrich_scan_result(base, tmp_path)

    assert enriched.confidence >= base.confidence


def test_enrichment_preserves_fields_the_graph_does_not_own(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("crewai\n")
    (tmp_path / "main.py").write_text(
        "import os\n"
        "from crewai import Agent\n\n"
        "def run_agent():\n"
        "    return os.getenv('OPENAI_API_KEY'), Agent\n"
    )
    base = scan(tmp_path)
    enriched = enrich_scan_result(base, tmp_path)

    assert enriched.dependencies == base.dependencies
    assert enriched.env_vars == base.env_vars
    assert enriched.tools == base.tools
    assert enriched.llm_clients == base.llm_clients
    assert enriched.risk_flags == base.risk_flags
    assert enriched.python_file_count == base.python_file_count
    assert enriched.detected_framework == base.detected_framework
    assert enriched.input_signatures == base.input_signatures
    assert enriched.detected_providers == base.detected_providers
    assert enriched.has_state_graph == base.has_state_graph
    assert enriched.graph_nodes == base.graph_nodes
    assert enriched.graph_edges == base.graph_edges
    assert enriched.repo_path == base.repo_path


def test_enrichment_promotes_the_graph_resolved_entry_point_to_first(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text(
        "def process():\n    return 1\n\ndef run_agent():\n    return process()\n"
    )
    base = scan(tmp_path)
    enriched = enrich_scan_result(base, tmp_path)

    assert enriched.primary_entry is not None
    assert enriched.primary_entry.function == "run_agent"
    # Reorders only -- no scanner-found candidate may be dropped.
    assert sorted(ep.function for ep in enriched.entry_points) == sorted(
        ep.function for ep in base.entry_points
    )


def test_enrichment_promotes_a_traced_entry_point_the_scanner_ranked_lower(
    tmp_path: Path,
) -> None:
    """The discriminating case for promotion.

    `run_agent` already wins scanner.py's own name ranking, so a fixture
    built around it can't tell enrichment apart from no enrichment at all.
    Here the scanner ranks `process` (70 + 20 filename) above `main`
    (50 + 20), but the graph *traces* the `__main__` guard and sees that
    `main` is what actually runs -- so only an enriched result puts `main`
    first.
    """
    (tmp_path / "app.py").write_text(
        "def process():\n"
        "    return 1\n\n\n"
        "def main():\n"
        "    return process()\n\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )
    base = scan(tmp_path)
    assert base.primary_entry is not None
    assert base.primary_entry.function == "process", "fixture no longer discriminates"

    enriched = enrich_scan_result(base, tmp_path)

    assert enriched.primary_entry is not None
    assert enriched.primary_entry.function == "main"


def test_enrichment_is_a_no_op_when_the_repo_cannot_be_graphed(tmp_path: Path) -> None:
    """A syntactically broken repo must degrade to the scanner's own result
    rather than failing the scan outright -- enrichment is strictly additive.
    """
    (tmp_path / "main.py").write_text("def broken(:\n    pass\n")
    base = scan(tmp_path)
    enriched = enrich_scan_result(base, tmp_path)

    assert enriched.detected_framework == base.detected_framework
    assert enriched.confidence == base.confidence


def test_enrichment_does_not_raise_confidence_when_frameworks_disagree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The graph may only *confirm* what the scanner already concluded.

    When the graph names a different framework than the scanner did, it is
    reasoning about something the scanner did not conclude, so its (possibly
    much higher) confidence must not be borrowed to inflate certainty in the
    scanner's answer. Only the `detection.framework == base.detected_framework`
    case is allowed to raise the score.
    """
    # langchain scores 0.75 + 0.1 (entry point) = 0.85 in the scanner, which
    # leaves headroom below 1.0 for a bogus raise to be observable.
    (tmp_path / "main.py").write_text(
        "from langchain.agents import initialize_agent\n\n"
        "def run_agent():\n"
        "    return initialize_agent\n"
    )
    base = scan(tmp_path)

    monkeypatch.setattr(
        "superrobot.pipeline.graph.enrich.detect_framework",
        lambda _repo_graph, _entry_point: FrameworkDetection(
            framework="definitely_not_what_the_scanner_said",
            confidence=1.0,
        ),
    )
    enriched = enrich_scan_result(base, tmp_path)

    assert base.confidence < 1.0, "fixture must leave headroom for a bogus raise to show up"
    assert enriched.confidence == base.confidence
    assert enriched.detected_framework == base.detected_framework


def _timeout_fixture(tmp_path: Path) -> None:
    """A repo where enrichment demonstrably changes the scanner's answer.

    The scanner ranks `process` (70 + 20 filename) above `main` (50 + 20),
    but the graph traces the `__main__` guard and promotes `main`; crewai is
    reachable from that traced entry point, so confidence rises too. Both
    effects must vanish when the graph build times out.
    """
    (tmp_path / "app.py").write_text(
        "from crewai import Agent\n\n\n"
        "def process():\n"
        "    return 1\n\n\n"
        "def main():\n"
        "    return process(), Agent\n\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )


def test_enrichment_returns_the_base_result_unchanged_when_the_graph_build_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exceeding the enrichment budget degrades to the scanner's own result.

    The guard must be all-or-nothing: no partially-built graph may leak into
    the result, so neither of enrichment's two effects (raised confidence,
    promoted entry point) may be half-applied.
    """
    _timeout_fixture(tmp_path)
    base = scan(tmp_path)

    # Control: with the real budget, enrichment *does* change the result --
    # otherwise the timeout assertions below would pass vacuously.
    enriched = enrich_scan_result(base, tmp_path)
    assert enriched.primary_entry is not None
    assert enriched.primary_entry.function == "main", "fixture no longer discriminates"

    # A negative budget puts the deadline in the past before jedi ever runs.
    monkeypatch.setattr("superrobot.pipeline.graph.enrich.ENRICHMENT_BUDGET_SECONDS", -1.0)
    timed_out = enrich_scan_result(base, tmp_path)

    assert timed_out == base
    assert timed_out.confidence == base.confidence
    assert [ep.function for ep in timed_out.entry_points] == [
        ep.function for ep in base.entry_points
    ]


def test_enrichment_degrades_when_build_repo_graph_raises_the_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GraphBuildTimeout` must be handled by `enrich_scan_result`, not
    escape it -- enrichment never raises, whatever the reason for failure.
    """
    _timeout_fixture(tmp_path)
    base = scan(tmp_path)

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise GraphBuildTimeout("graph build exceeded its deadline")

    monkeypatch.setattr("superrobot.pipeline.graph.enrich.build_repo_graph", _raise)

    assert enrich_scan_result(base, tmp_path) == base
