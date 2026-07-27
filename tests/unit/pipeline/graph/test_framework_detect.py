"""Tests for reachability-weighted framework detection."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_unknown_fallback_confidence_includes_entry_bonus(tmp_path: Path) -> None:
    """The module's own docstring/comments state the entry-signal bonus
    exists specifically so confidence here is never lower than scanner.py's
    for the same repo. scanner._compute_confidence() unconditionally adds
    +0.1 to the "unknown" base whenever any entry-point-shaped function
    exists -- regardless of whether a framework was found. Reproduce a repo
    with a function shaped like a real entry point (name "run_agent", in
    ENTRY_POINT_NAMES) and no framework import at all, so the "unknown"
    fallback path is hit. The graph-based path must apply the exact same
    bonus as every other return path in detect_framework(), landing on
    0.3, not the previously-hardcoded 0.2.

    Checked on both of detect_framework's reachability modes, since the
    bonus is computed differently in each: with a resolved entry point
    (`reachable` is narrowed to the call graph, and run_agent has to be
    found inside it) and with entry_point=None (`reachable` is empty and
    every function node counts). This repo has no __main__ guard and no
    console script, so its entry point comes from entry_points.py's
    tier-3 name heuristic.
    """
    (tmp_path / "main.py").write_text("def run_agent():\n    return 1\n")
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)
    assert entry == "main.run_agent"

    result = detect_framework(repo_graph, entry)

    assert result.framework == "unknown"
    assert result.confidence == pytest.approx(0.3)

    unresolved_result = detect_framework(repo_graph, None)

    assert unresolved_result.framework == "unknown"
    assert unresolved_result.confidence == pytest.approx(0.3)


def test_reachable_framework_tie_break_is_deterministic(tmp_path: Path) -> None:
    """When two frameworks are simultaneously reachable from the entry
    point, the winner must not depend on incidental graph-node insertion
    order (itself driven by iter_python_files' rglob filesystem
    enumeration order). This repo is built so that, absent a deterministic
    tie-break, the "crewai" module node is inserted into the graph before
    the "langgraph.graph" node (alphabetical file processing: a_crewai.py,
    then z_langgraph.py, then main.py) -- reproducing the exact scenario
    that used to make detect_framework() return "crewai". The correct,
    deterministic result must match FRAMEWORK_IMPORTS' own declaration
    order (superrobot.pipeline.scanner.FRAMEWORK_IMPORTS lists "langgraph"
    before "crewai"), i.e. "langgraph", regardless of insertion order.
    """
    (tmp_path / "a_crewai.py").write_text(
        "from crewai import Agent\n\ndef use_crewai():\n    return Agent\n"
    )
    (tmp_path / "z_langgraph.py").write_text(
        "from langgraph.graph import StateGraph\n\ndef use_langgraph():\n    return StateGraph\n"
    )
    (tmp_path / "main.py").write_text(
        "from a_crewai import use_crewai\n"
        "from z_langgraph import use_langgraph\n\n"
        "def run_agent():\n"
        "    use_crewai()\n"
        "    use_langgraph()\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework == "langgraph"


def test_langchain_beats_unrelated_framework_when_no_langgraph_is_involved(
    tmp_path: Path,
) -> None:
    """Regression test for the blanket "langchain always loses" tie-break
    bug: langchain must ONLY be demoted in favor of "langgraph" (the one
    real, narrow special case -- see _LANGGRAPH_BEATS_LANGCHAIN's comment
    in framework_detect.py), never in favor of an unrelated framework it
    happens to tie with. Here a `langchain_core.tools.tool` import is
    reachable alongside an unrelated reachable `crewai` import, with no
    langgraph anywhere in the repo. Since FRAMEWORK_IMPORTS declares
    "langchain" before "crewai", and there is no langgraph signal to
    justify demoting it, "langchain" must win the tie -- reporting
    "crewai" here would be exactly the false positive the blanket
    demotion produced (this would have failed before the fix, since the
    old code unconditionally pushed "langchain" to the very end of the
    priority order regardless of whether langgraph was involved).
    """
    (tmp_path / "a_crewai.py").write_text(
        "from crewai import Agent\n\ndef use_crewai():\n    return Agent\n"
    )
    (tmp_path / "z_langchain.py").write_text(
        "from langchain_core.tools import tool\n\ndef use_langchain():\n    return tool\n"
    )
    (tmp_path / "main.py").write_text(
        "from a_crewai import use_crewai\n"
        "from z_langchain import use_langchain\n\n"
        "def run_agent():\n"
        "    use_crewai()\n"
        "    use_langchain()\n\n"
        "if __name__ == '__main__':\n"
        "    run_agent()\n"
    )
    repo_graph = build_repo_graph(tmp_path)
    entry = resolve_entry_point(repo_graph)

    result = detect_framework(repo_graph, entry)

    assert result.framework == "langchain"
