"""run_gap_analysis should surface graph-detected unreachable imports."""

from __future__ import annotations

from pathlib import Path

from superrobot.models.gap_result import GapReport
from superrobot.pipeline.gap_analysis import run_gap_analysis


def _write_minimal_package(tmp_path: Path) -> Path:
    package_dir = tmp_path / "pkg"
    (package_dir / "agent" / "agent").mkdir(parents=True)
    (package_dir / "agent" / "agent" / "custom.py").write_text("# generated\n")
    return package_dir


def _unreachable_messages(report: GapReport) -> list[str]:
    return [f.message for f in report.findings if f.rule == "unreachable-framework-import"]


def test_reports_unreachable_framework_import_from_source_repo(
    tmp_path: Path,
) -> None:
    package_dir = _write_minimal_package(tmp_path)

    source_repo = tmp_path / "src"
    source_repo.mkdir()
    (source_repo / "dead_code.py").write_text(
        "from crewai import Agent\n\ndef unused():\n    return Agent\n"
    )
    (source_repo / "main.py").write_text(
        "from langgraph.graph import StateGraph\n\ndef run_agent():\n    return StateGraph\n"
    )

    report = run_gap_analysis(package_dir, source_repo=source_repo)

    unreachable = [f for f in report.findings if f.rule == "unreachable-framework-import"]
    assert unreachable, "expected an unreachable-framework-import finding"
    assert all(f.severity == "warning" for f in unreachable)
    assert any("crewai" in f.message for f in unreachable)


def test_no_unreachable_findings_when_no_source_repo_is_given(tmp_path: Path) -> None:
    package_dir = _write_minimal_package(tmp_path)

    report = run_gap_analysis(package_dir)

    assert not _unreachable_messages(report)


def test_no_false_positive_for_multi_hop_reachable_framework(tmp_path: Path) -> None:
    """A framework imported two hops down a real import chain is NOT dead code.

    Shape: main.run_agent (the entry point) calls into module `b`, `b`
    imports module `c`, and `c` imports the framework. Importing main
    genuinely executes the crewai import, so flagging it would tell the
    user to delete an import their code actually needs. This exact shape
    was a confirmed false positive earlier in this effort -- guard it.
    """
    package_dir = _write_minimal_package(tmp_path)

    source_repo = tmp_path / "src"
    source_repo.mkdir()
    (source_repo / "c.py").write_text(
        "from crewai import Agent\n\n\ndef build_agent():\n    return Agent\n"
    )
    (source_repo / "b.py").write_text(
        "from c import build_agent\n\n\ndef helper():\n    return build_agent()\n"
    )
    (source_repo / "main.py").write_text(
        "from b import helper\n\n\ndef run_agent():\n    return helper()\n"
    )

    report = run_gap_analysis(package_dir, source_repo=source_repo)

    assert not _unreachable_messages(report), (
        "crewai is reachable via main -> b -> c; it must not be reported as unreachable"
    )
