"""Reaching-definitions for values that flow into call parameters."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.graph.dataflow import resolve_parameter_values


def _repo(tmp_path: Path, source: str) -> Path:
    (tmp_path / "main.py").write_text(source)
    return tmp_path


def test_resolves_a_literal_argument(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'llm = ChatOpenAI(model="gpt-4o")\n')

    values = resolve_parameter_values(build_repo_graph(repo), "ChatOpenAI", "model")

    assert values.resolved == ["gpt-4o"]
    assert values.unresolved == []


def test_resolves_through_a_local_variable(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'name = "gpt-4o"\nllm = ChatOpenAI(model=name)\n')

    values = resolve_parameter_values(build_repo_graph(repo), "ChatOpenAI", "model")

    assert values.resolved == ["gpt-4o"]


def test_resolves_through_an_aliased_class(tmp_path: Path) -> None:
    """The case regex provably cannot handle."""
    repo = _repo(tmp_path, 'CLS = ChatOpenAI\nllm = CLS(model="gpt-4o")\n')

    values = resolve_parameter_values(build_repo_graph(repo), "ChatOpenAI", "model")

    assert values.resolved == ["gpt-4o"]


def test_reports_an_unresolvable_value_rather_than_guessing(tmp_path: Path) -> None:
    """A value from runtime config cannot be known statically. It must be
    reported as unresolved -- never silently omitted, never guessed.
    """
    repo = _repo(tmp_path, 'import os\nllm = ChatOpenAI(model=os.environ["MODEL"])\n')

    values = resolve_parameter_values(build_repo_graph(repo), "ChatOpenAI", "model")

    assert values.resolved == []
    assert values.unresolved, "an unresolvable value must be reported explicitly"
    assert "MODEL" in values.unresolved[0].expression


def test_every_finding_carries_provenance(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'llm = ChatOpenAI(model="gpt-4o")\n')

    values = resolve_parameter_values(build_repo_graph(repo), "ChatOpenAI", "model")

    assert values.sites, "must record where each value was found"
    site = values.sites[0]
    assert site.file.endswith("main.py")
    assert site.line == 1
