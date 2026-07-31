"""Project the Migration IR into DataRobot's `agent_spec.md`.

`agent_spec.md` is a *greenfield design* artifact: it says what agent you
want built. The Migration IR says what the source agent actually is. The
projection between them is therefore lossy by nature -- the spec has no
representation for control-flow topology, state backends, or prompt
provenance -- so the job here is not to squeeze the IR into the spec, it is
to emit the spec **and name everything that did not fit**.

Authoritative shape, confirmed against the vendored skill rather than
inferred: `references/agent-spec-examples.md` for the YAML, and
`scripts/rehearsal.py` (`EXTRACT_TOOL`) for what is genuinely required --
top-level `model`, `system_prompt`, `tools`, `examples`, each tool carrying
`function_name`, `inputs`, and `out`.

The projection refuses rather than improvises. It raises `ProjectionError`
when the coverage ledger is not clean, when the model was never resolved,
when there is no system prompt, and when a parameter type cannot be
expressed. Every one of those is a case where emitting a plausible-looking
spec would produce an agent that quietly does something other than the one
we were asked to migrate -- which is the exact failure this architecture
was built to eliminate.
"""

from __future__ import annotations

import re

import yaml

from superrobot.ir.model import MigrationIR, Tool, ToolParam

# The types `agent_spec.md` can express, per `rehearsal.py`'s TYPE_MAP.
_SPEC_TYPES = ("str", "int", "float", "bool", "list", "dict")

# Spellings that mean one of the above. `rehearsal.py` silently falls back
# to "string" for anything it does not recognize; we would rather name the
# mismatch than inherit a silent coercion.
_TYPE_ALIASES = {
    "string": "str",
    "text": "str",
    "integer": "int",
    "number": "float",
    "double": "float",
    "boolean": "bool",
    "array": "list",
    "sequence": "list",
    "tuple": "list",
    "object": "dict",
    "mapping": "dict",
    "any": "str",
}

# `list[str]`, `List[str]`, `Dict[str, Any]`, `Optional[int]` -- the
# subscript carries information agent_spec has no field for, so it is
# dropped here and the outer type is what survives.
_SUBSCRIPT = re.compile(r"^([A-Za-z_][\w.]*)\s*\[.*\]$", re.DOTALL)
_OPTIONAL = re.compile(r"^(?:typing\.)?Optional$", re.IGNORECASE)


class ProjectionError(Exception):
    """The IR cannot be projected without inventing something. Reported to
    the caller instead of resolved by guessing.
    """


def _normalize_type(raw: str, *, tool: str, param: str) -> str:
    """Map a Python-ish type name onto one `agent_spec.md` can express."""
    candidate = re.sub(r"\s*\|\s*None\b", "", raw.strip()) or raw.strip()

    match = _SUBSCRIPT.match(candidate)
    if match:
        outer = match.group(1)
        # `Optional[X]` is `X`, not a container.
        if _OPTIONAL.match(outer):
            inner = candidate[len(outer) :].strip()[1:-1].split(",")[0]
            return _normalize_type(inner, tool=tool, param=param)
        candidate = outer

    candidate = candidate.rsplit(".", 1)[-1]
    lowered = candidate.lower()

    if lowered in _SPEC_TYPES:
        return lowered
    if lowered in _TYPE_ALIASES:
        return _TYPE_ALIASES[lowered]

    raise ProjectionError(
        f"tool {tool!r} parameter {param!r} has type {raw!r}, which agent_spec.md "
        f"cannot express (it supports {', '.join(_SPEC_TYPES)}). Normalize it in "
        "the IR or record it as residue -- do not let it through as a guess."
    )


def _is_optional(raw: str) -> bool:
    """`Optional[X]` or `X | None` -- either way agent_spec.md cannot say so."""
    candidate = raw.strip()
    if re.search(r"\|\s*None\b", candidate):
        return True
    match = _SUBSCRIPT.match(candidate)
    return bool(match and _OPTIONAL.match(match.group(1).rsplit(".", 1)[-1]))


def _param(param: ToolParam, *, tool: str) -> dict[str, object]:
    entry: dict[str, object] = {
        "arg_name": param.name,
        "type": _normalize_type(param.type, tool=tool, param=param.name),
    }
    if param.object_schema is not None:
        # agent_spec.md's object_schema is prose, not a JSON Schema.
        entry["object_schema"] = _describe_schema(param.object_schema)
    return entry


def _describe_schema(schema: dict[str, object]) -> str:
    if len(schema) == 1:
        (only,) = schema.values()
        if isinstance(only, str):
            return only
    return ", ".join(f"{k}: {v}" for k, v in schema.items())


def _tool(tool: Tool) -> dict[str, object]:
    entry: dict[str, object] = {
        "function_name": tool.name,
        "inputs": [_param(p, tool=tool.name) for p in tool.inputs],
        "out": [_param(p, tool=tool.name) for p in tool.outputs],
    }
    if tool.description:
        entry["description"] = tool.description
    if tool.auth is not None:
        entry["auth_spec"] = {
            "service_name": tool.auth.service_name,
            "auth_method": tool.auth.auth_method,
        }
    return entry


def _require_clean_coverage(ir: MigrationIR) -> None:
    if ir.coverage is None:
        raise ProjectionError(
            "refusing to project: no coverage ledger was recorded, so nothing "
            "establishes that the IR accounts for what the probes found."
        )
    if ir.coverage.is_clean():
        return

    gaps = [f"{f.id} ({f.kind}) at {f.file}:{f.line}" for f in ir.coverage.unaccounted]
    gaps += [
        f"{e.fact.id} ({e.fact.kind}) at {e.fact.file}:{e.fact.line}: {e.reason}"
        for e in ir.coverage.entries
        if e.disposition.value == "blocking"
    ]
    raise ProjectionError(
        "refusing to project an agent_spec from an incomplete understanding. "
        "Unresolved source facts:\n  " + "\n  ".join(gaps)
    )


def _require_model(ir: MigrationIR) -> str:
    model = ir.primary_model()
    if model:
        return model

    unresolved = [expr for call in ir.llm_calls for expr in call.unresolved_model]
    detail = (
        " The dataflow probe could not resolve: " + ", ".join(unresolved)
        if unresolved
        else " No LLM call site carried a resolvable model."
    )
    raise ProjectionError(
        "refusing to project: agent_spec.md requires a model and none was "
        "resolved statically." + detail
    )


def _residue_notes(ir: MigrationIR, chosen_model: str) -> list[str]:
    """Everything the spec has no field for. Emitted as leading comments so
    it travels with the artifact instead of living only in our own report.
    """
    notes: list[str] = []

    # `rehearsal.py`'s build_tool_definitions marks every input required, so
    # an optional parameter silently becomes mandatory in the migrated agent.
    optional = [
        f"{tool.name}.{p.name}" for tool in ir.tools for p in tool.inputs if _is_optional(p.type)
    ]
    if optional:
        notes.append(
            "optional tool parameter(s) "
            + ", ".join(optional)
            + " become required; agent_spec.md has no optional marker"
        )

    extra_models = sorted({c.model for c in ir.llm_calls if c.model and c.model != chosen_model})
    if extra_models:
        notes.append(
            "the source agent also uses "
            + ", ".join(extra_models)
            + "; agent_spec.md names a single model"
        )

    if ir.orchestration and ir.orchestration.nodes:
        notes.append(
            f"orchestration topology ({ir.orchestration.kind.value}, "
            f"{len(ir.orchestration.nodes)} node(s)) has no agent_spec.md representation"
        )
    for item in ir.state:
        notes.append(f"state {item.name!r} ({item.kind}) has no agent_spec.md representation")
    for entry in ir.residue:
        notes.append(f"{entry.severity.value}: {entry.description} -- {entry.reason}")
    if not ir.examples:
        notes.append("no example inputs were extracted; rehearsal.py will have nothing to replay")

    return notes


def migration_ir_to_agent_spec(ir: MigrationIR) -> str:
    """Render `agent_spec.md` from a Migration IR.

    Raises `ProjectionError` rather than emitting anything derived from a
    gap, a guess, or an unclean ledger.
    """
    _require_clean_coverage(ir)
    model = _require_model(ir)

    if not (ir.system_prompt and ir.system_prompt.strip()):
        raise ProjectionError(
            "refusing to project: agent_spec.md requires a system_prompt and the "
            "IR has none. Writing one here would change what the agent does."
        )

    spec: dict[str, object] = {
        "model": model,
        "system_prompt": ir.system_prompt.strip(),
        "tools": [_tool(t) for t in ir.tools],
        "examples": list(ir.examples),
        "frontend": ir.frontend.model_dump(exclude_none=True, exclude_defaults=False)
        if ir.frontend.type != "chat"
        else {"type": "chat"},
    }

    body = yaml.safe_dump(spec, sort_keys=False, default_flow_style=False, allow_unicode=True)

    notes = _residue_notes(ir, model)
    if not notes:
        return body

    header = [f"# Migrated from {ir.source_repo}", "# Not represented in this spec:"]
    header += [f"#   - {note}" for note in notes]
    return "\n".join(header) + "\n" + body
