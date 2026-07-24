"""Tests for reachability-weighted framework detection."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.entry_points import resolve_entry_point
from superrobot.pipeline.graph.framework_detect import detect_framework


def test_detects_reachable_framework_with_high_confidence(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from langgraph.graph import StateGraph\n\n"
        "def run_agent():\n"
        "    return StateGraph\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework == "langgraph"
    assert result.confidence >= 0.9
    assert result.unreachable_warnings == []


def test_flags_unreachable_framework_import_separately(tmp_path: Path) -> None:
    (tmp_path / "dead_code.py").write_text(
        "from crewai import Agent\n\ndef unused():\n    return Agent\n"
    )
    (tmp_path / "main.py").write_text(
        "from langgraph.graph import StateGraph\n\n"
        "def run_agent():\n"
        "    return StateGraph\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework == "langgraph"
    assert any("crewai" in warning for warning in result.unreachable_warnings)


def test_detects_reachable_framework_with_nested_entry_point(tmp_path: Path) -> None:
    """Regression test: the entry point's containing module must be found via
    real 'defines' graph edges, not by naively splitting the entry point id on
    its first '.'. A nested entry point like "pkg.sub.run_agent" lives in
    module "pkg.sub" -- string-splitting on the first dot would incorrectly
    look for a module node named "pkg", which doesn't exist in the graph.
    """
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "sub.py").write_text(
        "from langgraph.graph import StateGraph\n\n"
        "def run_agent():\n"
        "    return StateGraph\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    assert entry == "pkg.sub.run_agent"

    result = detect_framework(repo_graph, entry)

    assert result.framework == "langgraph"
    assert result.confidence >= 0.9
    assert result.unreachable_warnings == []


def test_type_checking_only_import_is_not_counted_as_framework_detected(tmp_path: Path) -> None:
    """A framework referenced ONLY inside `if TYPE_CHECKING:` never actually
    executes, so it must not be treated as "in use". Here crewai's only
    graph presence is a type-checking-only edge from the entry module, and
    there is no other framework anywhere in the repo -- detect_framework()
    must NOT report "crewai" (that would be a false positive driven by code
    that never runs). We also assert it doesn't get folded into
    unreachable_warnings: unlike a genuinely unreachable import (dead code
    that might be a leftover migration), a TYPE_CHECKING-guarded import was
    never meant to execute in the first place, so "unreachable framework
    import ... confirm this isn't leftover from an abandoned migration"
    would be inaccurate, misleading language for it. We therefore skip it
    from detection entirely rather than warn about it under either label.
    """
    (tmp_path / "main.py").write_text(
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n"
        "    from crewai import Agent\n\n"
        "def run_agent():\n"
        "    return 1\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework != "crewai"
    assert not any("crewai" in warning for warning in result.unreachable_warnings)


def test_type_checking_only_import_does_not_shadow_real_reachable_framework(
    tmp_path: Path,
) -> None:
    """Companion to the test above, matching the exact shape described in
    the bug report: a real, executed langgraph import alongside a
    crewai import that only lives inside `if TYPE_CHECKING:`.
    detect_framework() must report the real framework (langgraph), never
    the type-checking-only one (crewai)."""
    (tmp_path / "main.py").write_text(
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n"
        "    from crewai import Agent\n\n"
        "from langgraph.graph import StateGraph\n\n"
        "def run_agent():\n"
        "    return StateGraph\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework == "langgraph"
    assert not any("crewai" in warning for warning in result.unreachable_warnings)


def test_returns_unknown_with_low_confidence_when_no_framework_found(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x = 1\n")
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework == "unknown"
    assert result.confidence <= 0.3
