"""Projection of the Migration IR into DataRobot's `agent_spec.md`.

The authoritative shape is
`vendor/datarobot-agent-skills/skills/datarobot-agent-assist/` --
`references/agent-spec-examples.md` for the YAML, and `scripts/rehearsal.py`
(`EXTRACT_TOOL`) for what is actually required: top-level `model`,
`system_prompt`, `tools`, `examples`, with each tool requiring
`function_name`, `inputs`, and `out`.
"""

from __future__ import annotations

import pytest
import yaml

from superrobot.ir.agent_spec import ProjectionError, migration_ir_to_agent_spec
from superrobot.ir.model import (
    Coverage,
    CoverageEntry,
    Disposition,
    Evidence,
    LlmCall,
    MigrationIR,
    SourceFact,
    Tool,
    ToolAuth,
    ToolParam,
)


def _evidence() -> Evidence:
    return Evidence(file="main.py", line=1, node_id="main::n")


def _clean_coverage() -> Coverage:
    fact = SourceFact(id="llm-1", kind="llm_call", description="ChatOpenAI", file="main.py", line=1)
    return Coverage(entries=[CoverageEntry(fact=fact, disposition=Disposition.MIGRATED)])


def _ir(**overrides: object) -> MigrationIR:
    defaults: dict[str, object] = {
        "source_repo": "/repo",
        "name": "research-agent",
        "system_prompt": "You are a research assistant. Always cite sources.",
        "examples": ["Find recent papers on LLM hallucination"],
        "llm_calls": [LlmCall(client="ChatOpenAI", model="gpt-4o", evidence=[_evidence()])],
        "tools": [
            Tool(
                name="search_docs",
                callable="tools.search_docs",
                inputs=[
                    ToolParam(name="query", type="str"),
                    ToolParam(name="top_k", type="int"),
                ],
                outputs=[
                    ToolParam(
                        name="documents",
                        type="list",
                        object_schema={"item": "{title: str, url: str}"},
                    )
                ],
                auth=ToolAuth(service_name="Internal KB", auth_method="bearer_token"),
                evidence=[_evidence()],
            )
        ],
        "coverage": _clean_coverage(),
    }
    defaults.update(overrides)
    return MigrationIR(**defaults)  # type: ignore[arg-type]


def test_the_projection_round_trips_through_safe_load() -> None:
    spec = yaml.safe_load(migration_ir_to_agent_spec(_ir()))

    assert set(spec) >= {"model", "system_prompt", "tools", "examples", "frontend"}


def test_it_carries_the_model_the_dataflow_probe_resolved() -> None:
    spec = yaml.safe_load(migration_ir_to_agent_spec(_ir()))

    assert spec["model"] == "gpt-4o"


def test_each_ir_tool_becomes_one_spec_tool_with_matching_arg_names() -> None:
    spec = yaml.safe_load(migration_ir_to_agent_spec(_ir()))

    (tool,) = spec["tools"]
    assert tool["function_name"] == "search_docs"
    assert [i["arg_name"] for i in tool["inputs"]] == ["query", "top_k"]
    assert [i["type"] for i in tool["inputs"]] == ["str", "int"]
    assert [o["arg_name"] for o in tool["out"]] == ["documents"]
    assert tool["auth_spec"] == {
        "service_name": "Internal KB",
        "auth_method": "bearer_token",
    }


def test_a_tool_with_no_auth_omits_auth_spec() -> None:
    ir = _ir(
        tools=[
            Tool(
                name="now",
                callable="tools.now",
                outputs=[ToolParam(name="ts")],
                evidence=[_evidence()],
            )
        ]
    )

    (tool,) = yaml.safe_load(migration_ir_to_agent_spec(ir))["tools"]

    assert "auth_spec" not in tool
    assert tool["inputs"] == []


def test_it_refuses_to_emit_a_spec_when_the_ledger_is_not_clean() -> None:
    """A spec built from an incomplete understanding is the silently-wrong
    output this whole architecture exists to prevent. Blocking beats
    emitting something plausible.
    """
    unaccounted = SourceFact(
        id="llm-2", kind="llm_call", description="ChatFireworks", file="other.py", line=9
    )
    ir = _ir(coverage=Coverage(entries=[], unaccounted=[unaccounted]))

    with pytest.raises(ProjectionError) as excinfo:
        migration_ir_to_agent_spec(ir)

    assert "llm-2" in str(excinfo.value)


def test_it_refuses_when_no_coverage_was_recorded_at_all() -> None:
    """No ledger is worse than a dirty one: nothing was ever checked."""
    with pytest.raises(ProjectionError):
        migration_ir_to_agent_spec(_ir(coverage=None))


def test_it_refuses_when_the_model_could_not_be_resolved() -> None:
    ir = _ir(
        llm_calls=[
            LlmCall(
                client="ChatOpenAI",
                model=None,
                unresolved_model=['os.environ["MODEL"]'],
                evidence=[_evidence()],
            )
        ]
    )

    with pytest.raises(ProjectionError) as excinfo:
        migration_ir_to_agent_spec(ir)

    assert "MODEL" in str(excinfo.value), "the blocker must name the expression that defeated us"


def test_it_refuses_when_there_is_no_system_prompt() -> None:
    """`agent_spec.md` requires it and inventing one would change what the
    agent does.
    """
    with pytest.raises(ProjectionError):
        migration_ir_to_agent_spec(_ir(system_prompt=None))


def test_it_refuses_a_param_type_agent_spec_cannot_express() -> None:
    ir = _ir(
        tools=[
            Tool(
                name="load",
                callable="tools.load",
                inputs=[ToolParam(name="frame", type="pandas.DataFrame")],
                evidence=[_evidence()],
            )
        ]
    )

    with pytest.raises(ProjectionError) as excinfo:
        migration_ir_to_agent_spec(ir)

    assert "pandas.DataFrame" in str(excinfo.value)


def test_common_python_type_aliases_are_normalized() -> None:
    ir = _ir(
        tools=[
            Tool(
                name="load",
                callable="tools.load",
                inputs=[
                    ToolParam(name="a", type="string"),
                    ToolParam(name="b", type="List[str]"),
                    ToolParam(name="c", type="Dict[str, Any]"),
                    ToolParam(name="d", type="boolean"),
                ],
                evidence=[_evidence()],
            )
        ]
    )

    (tool,) = yaml.safe_load(migration_ir_to_agent_spec(ir))["tools"]

    assert [i["type"] for i in tool["inputs"]] == ["str", "list", "dict", "bool"]


@pytest.mark.parametrize("declared", ["Optional[int]", "int | None", "typing.Optional[int]"])
def test_an_optional_parameter_projects_to_its_inner_type(declared: str) -> None:
    ir = _ir(
        tools=[
            Tool(
                name="page",
                callable="tools.page",
                inputs=[ToolParam(name="limit", type=declared)],
                evidence=[_evidence()],
            )
        ]
    )

    (tool,) = yaml.safe_load(migration_ir_to_agent_spec(ir))["tools"]

    assert tool["inputs"][0]["type"] == "int"


def test_lost_optionality_is_named_rather_than_swallowed() -> None:
    """rehearsal.py marks every spec input required, so an optional argument
    silently becomes mandatory in the migrated agent.
    """
    ir = _ir(
        tools=[
            Tool(
                name="page",
                callable="tools.page",
                inputs=[ToolParam(name="limit", type="Optional[int]")],
                evidence=[_evidence()],
            )
        ]
    )

    rendered = migration_ir_to_agent_spec(ir)

    assert "page.limit" in rendered
    assert "required" in rendered


def test_extra_resolved_models_are_recorded_rather_than_dropped() -> None:
    """An agent using two models projects to a spec that can name only one.
    The other must appear in the spec's own residue comment, not vanish.
    """
    ir = _ir(
        llm_calls=[
            LlmCall(client="ChatOpenAI", model="gpt-4o", evidence=[_evidence()]),
            LlmCall(client="ChatAnthropic", model="claude-opus-4-8", evidence=[_evidence()]),
        ]
    )

    rendered = migration_ir_to_agent_spec(ir)

    assert yaml.safe_load(rendered)["model"] == "gpt-4o"
    assert "claude-opus-4-8" in rendered
