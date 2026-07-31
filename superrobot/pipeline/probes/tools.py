"""Find every function the agent exposes to the model as a tool.

A tool the migration misses is a capability the rebuilt agent silently
loses, and no test will catch it -- the agent simply stops being able to do
something. So this probe is tuned the same way `llm_calls` is: over-report
with an honest `detection` rather than stay quiet.

Detection is AST-based over the code property graph. Every decorator and
every constructor is resolved through the module's import/alias bindings
(`graph.dataflow.resolve_callee_name`) before it is classified, so
`@lc_tool` where `lc_tool` came from `from langchain_core.tools import tool
as lc_tool` is recognized, and the characters `@tool` inside a docstring
are not.

The rules, and what each one is worth
-------------------------------------
Each `ToolSite` records in `detection` *which rule fired*, because the rules
differ enormously in how much they prove. In rough order of confidence:

``decorator:<resolved dotted name>``
    A decorator whose local name is one of `_TOOL_DECORATORS`
    (`tool`, `function_tool`, `kernel_function`, `tool_plain`). The dotted
    name is the *resolved* one, so a reviewer can tell
    ``decorator:langchain_core.tools.tool`` (imported from a known tool
    package -- near-certain) from a bare ``decorator:tool`` (a decorator
    named `tool` that we could not resolve to any import -- probably still a
    tool, but the evidence is weaker).

``constructor:<Class>.<factory>``
    An explicit tool object built from a function --
    `StructuredTool.from_function(...)`, `Tool(...)`,
    `FunctionTool.from_defaults(...)`. The wrapped callable is named right
    there, so this is as strong as a decorator.

``tools_kwarg:<callee>``
    The value appeared inside a `tools=[...]` argument, or the first
    positional argument of `bind_tools(...)`. Whatever `<callee>` is --
    `Agent`, `Task`, `CodeAgent`, `create`, a name we have never heard of --
    an argument literally named `tools` holding a callable is a tool. This
    also covers raw provider tool schemas (`tools=[{"type": "function",
    "function": {"name": ...}}]`), which are dicts rather than callables.

``tools_kwarg_unresolved:<callee>``
    A `tools=` argument that is not a literal sequence -- `tools=get_tools()`,
    `tools=TOOLSET`. The toolset cannot be enumerated statically, but a
    reviewer must be told it exists, so one low-confidence site carrying the
    source expression is emitted rather than nothing. This is the one
    detection that does *not* assert a specific tool.

Signatures come from the definition, never from the call site. When a
reference resolves to a function defined in the repo, its parameters,
annotations, return annotation, docstring, and async-ness are read off that
`FunctionDef`. When it does not (a third-party
`DuckDuckGoSearchTool()`, a `functools.partial`, an object built at
runtime), the site is still emitted with what is known and empty
inputs/outputs -- an unintrospectable tool is a fact, not a reason to drop
one.

Annotations are captured exactly as written (`ast.unparse` of the
annotation node). Mapping them onto `agent_spec.md`'s type vocabulary is
`ir/agent_spec.py`'s job; doing it here would mean two places disagree about
what `list[int] | None` is. An unannotated parameter carries `type=None`.
Defaulting it to `"string"` would manufacture a type the source never
stated.

It deliberately does NOT catch
------------------------------
* tool *classes* -- `class SearchTool(BaseTool)` with a `_run` method
  (LangChain), `class MyTool(Tool)` (smolagents). These are real and common;
  recognizing them needs base-class resolution through the graph, which is
  not done here.
* toolkits that expand to many tools at runtime
  (`toolkit.get_tools()`, `load_tools(["serpapi"])`), MCP servers mounted
  wholesale, and anything else where the tool list only exists at runtime.
  A `tools=` argument holding such a call is at least reported as
  `tools_kwarg_unresolved`; a bare `agent.add_tool(x)` style registration is
  not reported at all.
* tools registered by a method call rather than an argument --
  `kernel.add_plugin(...)`, `server.add_tool(...)`, `agent.register(...)`.
* the tool *descriptions* frameworks derive from Pydantic `args_schema`
  models, and per-argument descriptions from `Annotated[...,
  Field(description=...)]` or a Google-style docstring's Args block. Only
  the docstring's summary becomes `description`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from superrobot.pipeline.graph.builder import RepoGraph, _qualified_name
from superrobot.pipeline.graph.dataflow import (
    ModuleContext,
    Site,
    analyze_modules,
    resolve_callee_name,
)

_FUNCTION_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)

# Decorator *local* names that mark a function as a tool. Matched on the
# last segment of the resolved dotted name, which is what makes
# `@mcp.tool()`, `@agent.tool`, and `@server.tool_plain` work: the object
# the decorator hangs off is a runtime value we cannot resolve, but the
# attribute is the framework's own vocabulary.
_TOOL_DECORATORS = frozenset(
    {
        "tool",  # langchain_core.tools, llama_index, smolagents, crewai_tools, FastMCP
        "tool_plain",  # pydantic-ai
        "function_tool",  # openai-agents, pydantic-ai
        "kernel_function",  # semantic-kernel
    }
)

# Tool objects built from an existing function. Maps the callee (matched on
# a dotted-segment suffix of the resolved name) to the parameters that may
# carry the wrapped callable, in priority order. A callable passed
# positionally is picked up separately -- see `_wrapped_callable`.
_TOOL_CONSTRUCTORS: dict[str, tuple[str, ...]] = {
    "StructuredTool.from_function": ("func", "coroutine"),
    "StructuredTool": ("func", "coroutine"),
    "Tool.from_function": ("func", "coroutine"),
    "Tool": ("func", "coroutine"),
    "FunctionTool.from_defaults": ("fn", "async_fn"),
    "FunctionTool": ("fn", "func"),
}

# Callees whose *first positional* argument is a tool list. `tools=` as a
# keyword is recognized on any call at all, so only the positional-only
# spellings need naming.
_POSITIONAL_TOOL_LIST_CALLEES = frozenset({"bind_tools"})

# Parameters that name a tool at a construction site, in priority order.
_NAME_PARAMETERS = ("name", "func_name", "tool_name")
_DESCRIPTION_PARAMETERS = ("description", "desc")

# `self`/`cls` are the runtime receiver, never something the model fills in.
_IMPLICIT_PARAMETERS = frozenset({"self", "cls"})


@dataclass(frozen=True)
class ToolArg:
    """One input or output of a tool.

    `type` is the annotation exactly as written, or None when the source
    did not annotate it -- see the module docstring on why that is not
    defaulted.
    """

    name: str
    type: str | None = None


@dataclass
class ToolSite:
    """One tool the agent exposes, with the evidence for believing it is one.

    `detection` lists every rule that fired for this tool, comma-separated
    and sorted, so a tool found twice reads as stronger evidence rather than
    appearing twice.
    """

    name: str
    callable: str
    description: str | None
    inputs: list[ToolArg]
    outputs: list[ToolArg]
    is_async: bool
    decorator: str | None
    detection: str
    site: Site
    detections: frozenset[str] = field(default_factory=frozenset, repr=False, compare=False)

    @property
    def resolved(self) -> bool:
        """True when the tool's signature came from a definition in the repo
        rather than being left empty because we could not find one.
        """
        return bool(self.inputs or self.outputs or self.description or self.is_async)


def find_tool_sites(repo_graph: RepoGraph) -> list[ToolSite]:
    """Every tool the repo declares, in file/line order.

    Includes tools we cannot introspect and toolsets we cannot enumerate,
    each flagged by its `detection`, so the coverage ledger can block on
    them instead of a migration quietly shipping a less capable agent.
    """
    modules = analyze_modules(repo_graph)
    definitions = _index_definitions(modules)

    found: list[ToolSite] = []
    for module in modules:
        for node in ast.walk(module.tree):
            if isinstance(node, _FUNCTION_DEFS):
                found.extend(_from_decorators(module, node))
            elif isinstance(node, ast.Call):
                found.extend(_from_constructor(module, node, definitions))
                found.extend(_from_tool_lists(module, node, definitions))

    return _deduplicate(found)


# --------------------------------------------------------------------------
# Rule 1: decorators
# --------------------------------------------------------------------------


def _from_decorators(
    module: ModuleContext, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> list[ToolSite]:
    """Tools declared by decorating their implementation."""
    sites: list[ToolSite] = []
    for decorator in function.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        dotted = _dotted_name(module, call.func if call else decorator)
        if dotted is None or dotted.rsplit(".", 1)[-1] not in _TOOL_DECORATORS:
            continue
        sites.append(
            _from_definition(
                module,
                function,
                name=_explicit_name(call) if call else None,
                description=_explicit_description(call) if call else None,
                decorator=dotted,
                detection=f"decorator:{dotted}",
            )
        )
    return sites


# --------------------------------------------------------------------------
# Rule 2: tool objects constructed from a function
# --------------------------------------------------------------------------


def _from_constructor(
    module: ModuleContext, call: ast.Call, definitions: _DefinitionIndex
) -> list[ToolSite]:
    """`StructuredTool.from_function(...)` and friends."""
    callee = resolve_callee_name(module, call)
    if callee is None:
        return []
    matched = _matching_constructor(callee)
    if matched is None:
        return []
    wrapped = _wrapped_callable(call, _TOOL_CONSTRUCTORS[matched])
    if wrapped is None:
        return []
    return [
        _from_reference(
            module,
            wrapped,
            call,
            definitions,
            name=_explicit_name(call),
            description=_explicit_description(call),
            detection=f"constructor:{matched}",
        )
    ]


def _matching_constructor(callee: str) -> str | None:
    """The `_TOOL_CONSTRUCTORS` key this resolved callee names, if any.

    Matched on dotted-segment boundaries so `langchain_core.tools.Tool`
    matches `Tool` while `StructuredTool` does not.
    """
    for key in sorted(_TOOL_CONSTRUCTORS, key=len, reverse=True):
        if callee == key or callee.endswith(f".{key}"):
            return key
    return None


def _wrapped_callable(call: ast.Call, parameters: tuple[str, ...]) -> ast.expr | None:
    """The expression naming the function a tool object wraps.

    Keyword arguments are preferred because they are unambiguous; a purely
    positional call falls back to the first argument that is a name (not a
    string), which is how `Tool("search", run, ...)` spells it.
    """
    for parameter in parameters:
        for keyword in call.keywords:
            if keyword.arg == parameter:
                return keyword.value
    for argument in call.args:
        if isinstance(argument, (ast.Name, ast.Attribute)):
            return argument
    return None


# --------------------------------------------------------------------------
# Rule 3: `tools=[...]` wiring
# --------------------------------------------------------------------------


def _from_tool_lists(
    module: ModuleContext, call: ast.Call, definitions: _DefinitionIndex
) -> list[ToolSite]:
    """Anything handed to something as its tools.

    Deliberately not restricted to a list of known agent constructors: the
    frameworks are not enumerable, but the argument name is. The callee goes
    into `detection` so a reviewer can judge it.
    """
    callee = resolve_callee_name(module, call)
    label = callee.rsplit(".", 1)[-1] if callee else "?"

    lists: list[ast.expr] = [keyword.value for keyword in call.keywords if keyword.arg == "tools"]
    if callee is not None and label in _POSITIONAL_TOOL_LIST_CALLEES and call.args:
        lists.append(call.args[0])

    sites: list[ToolSite] = []
    for expression in lists:
        if not isinstance(expression, (ast.List, ast.Tuple, ast.Set)):
            # A toolset we cannot enumerate. One honest low-confidence site
            # beats silence; see the module docstring.
            sites.append(
                _opaque_site(
                    module,
                    expression,
                    name=ast.unparse(expression),
                    detection=f"tools_kwarg_unresolved:{label}",
                )
            )
            continue
        for element in expression.elts:
            site = _tool_list_element(module, element, definitions, label)
            if site is not None:
                sites.append(site)
    return sites


def _tool_list_element(
    module: ModuleContext,
    element: ast.expr,
    definitions: _DefinitionIndex,
    label: str,
) -> ToolSite | None:
    """One entry of a `tools=[...]` list."""
    detection = f"tools_kwarg:{label}"
    if isinstance(element, ast.Starred):
        return _opaque_site(
            module,
            element,
            name=ast.unparse(element),
            detection=f"tools_kwarg_unresolved:{label}",
        )
    if isinstance(element, ast.Dict):
        return _from_schema_dict(module, element, detection)
    if isinstance(element, ast.Constant) and isinstance(element.value, str):
        # `load_tools`-style named tools: a string is the whole declaration.
        return _opaque_site(module, element, name=element.value, detection=detection)
    return _from_reference(module, element, element, definitions, detection=detection)


def _from_schema_dict(module: ModuleContext, node: ast.Dict, detection: str) -> ToolSite | None:
    """A raw provider tool schema -- `{"type": "function", "function":
    {"name": ..., "description": ...}}` -- which declares a tool with no
    Python callable anywhere near it.
    """
    body = _dict_get(node, "function")
    schema = body if isinstance(body, ast.Dict) else node
    name = _dict_get(schema, "name")
    if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
        return None
    description = _dict_get(schema, "description")
    return ToolSite(
        name=name.value,
        callable=name.value,
        description=description.value
        if isinstance(description, ast.Constant) and isinstance(description.value, str)
        else None,
        inputs=[],
        outputs=[],
        is_async=False,
        decorator=None,
        detection=detection,
        detections=frozenset({detection}),
        site=module.site_for(node),
    )


def _dict_get(node: ast.Dict, key: str) -> ast.expr | None:
    for candidate, value in zip(node.keys, node.values, strict=True):
        if isinstance(candidate, ast.Constant) and candidate.value == key:
            return value
    return None


# --------------------------------------------------------------------------
# Turning a reference or a definition into a ToolSite
# --------------------------------------------------------------------------


def _from_reference(
    module: ModuleContext,
    reference: ast.expr,
    at: ast.expr,
    definitions: _DefinitionIndex,
    *,
    name: str | None = None,
    description: str | None = None,
    detection: str,
) -> ToolSite:
    """A tool named by an expression -- `search`, `tools.search.web_search`,
    `DuckDuckGoSearchTool()`.

    When the expression resolves to a function defined in this repo, the
    site is built from that definition (signature, docstring, async-ness).
    Otherwise it is built from the reference alone.
    """
    target = reference.func if isinstance(reference, ast.Call) else reference
    dotted = _dotted_name(module, target)
    found = _lookup(definitions, module, dotted) if dotted else None
    if found is not None:
        owner, function = found
        return _from_definition(
            owner,
            function,
            name=name,
            description=description,
            decorator=None,
            detection=detection,
        )
    return _opaque_site(
        module,
        at,
        name=name or (dotted or ast.unparse(target)).rsplit(".", 1)[-1],
        callable_name=dotted or ast.unparse(target),
        description=description,
        detection=detection,
    )


def _from_definition(
    module: ModuleContext,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    name: str | None,
    description: str | None,
    decorator: str | None,
    detection: str,
) -> ToolSite:
    """The fully-introspected case: we have the implementation in hand."""
    docstring = ast.get_docstring(function)
    return ToolSite(
        name=name or function.name,
        callable=_qualified_id(module, function),
        description=description or docstring,
        inputs=_inputs(function),
        outputs=_outputs(function),
        is_async=isinstance(function, ast.AsyncFunctionDef),
        decorator=decorator,
        detection=detection,
        detections=frozenset({detection}),
        site=module.site_for(function),
    )


def _opaque_site(
    module: ModuleContext,
    node: ast.expr,
    *,
    name: str,
    callable_name: str | None = None,
    description: str | None = None,
    detection: str,
) -> ToolSite:
    """A tool we can name but not introspect. Reported, never dropped."""
    return ToolSite(
        name=name,
        callable=callable_name if callable_name is not None else ast.unparse(node),
        description=description,
        inputs=[],
        outputs=[],
        is_async=False,
        decorator=None,
        detection=detection,
        detections=frozenset({detection}),
        site=module.site_for(node),
    )


def _inputs(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ToolArg]:
    """Every parameter the model can fill in, annotated as written.

    `*args`/`**kwargs` are included -- a tool taking them has an input
    surface we cannot describe, and hiding that would make the tool look
    simpler than it is.
    """
    args = function.args
    parameters = [
        *args.posonlyargs,
        *args.args,
        *([args.vararg] if args.vararg else []),
        *args.kwonlyargs,
        *([args.kwarg] if args.kwarg else []),
    ]
    return [
        ToolArg(name=parameter.arg, type=_annotation(parameter.annotation))
        for parameter in parameters
        if parameter.arg not in _IMPLICIT_PARAMETERS
    ]


def _outputs(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ToolArg]:
    """The declared return type, or nothing when none was declared.

    An unannotated return is reported as no known output rather than as an
    output of unknown type: there is exactly one return value either way, and
    the absence of the annotation is the fact.
    """
    annotation = _annotation(function.returns)
    return [ToolArg(name="return", type=annotation)] if annotation else []


def _annotation(node: ast.expr | None) -> str | None:
    """The annotation exactly as the source wrote it, or None."""
    return ast.unparse(node) if node is not None else None


def _explicit_name(call: ast.Call) -> str | None:
    """The tool name given at a declaration site -- `@tool("web-search")`,
    `StructuredTool.from_function(name="lookup")`.
    """
    for parameter in _NAME_PARAMETERS:
        literal = _string_keyword(call, parameter)
        if literal is not None:
            return literal
    for argument in call.args:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            return argument.value
    return None


def _explicit_description(call: ast.Call) -> str | None:
    """The description given at a declaration site, which overrides the
    docstring -- it is what the framework actually shows the model.
    """
    for parameter in _DESCRIPTION_PARAMETERS:
        literal = _string_keyword(call, parameter)
        if literal is not None:
            return literal
    return None


def _string_keyword(call: ast.Call, parameter: str) -> str | None:
    """The value of a keyword argument, when it is written as a string
    literal. A computed description is not resolved here -- the docstring
    remains the fallback.
    """
    for keyword in call.keywords:
        if (
            keyword.arg == parameter
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        ):
            return keyword.value.value
    return None


# --------------------------------------------------------------------------
# Resolving a name to a definition in the repo
# --------------------------------------------------------------------------

#: fully-qualified dotted name -> the module and def it names, plus a
#: bare-name index used only when the name is unambiguous repo-wide.
_DefinitionIndex = tuple[
    dict[str, tuple[ModuleContext, ast.FunctionDef | ast.AsyncFunctionDef]],
    dict[str, tuple[ModuleContext, ast.FunctionDef | ast.AsyncFunctionDef] | None],
]


def _index_definitions(modules: list[ModuleContext]) -> _DefinitionIndex:
    """Index every function in the repo by its fully-qualified dotted name.

    The bare-name index exists for references the alias map cannot fully
    resolve -- notably relative imports, which `dataflow._build_aliases`
    deliberately leaves alone. A bare name bound to more than one definition
    maps to None: guessing which one a `tools=[search]` meant would be
    exactly the kind of invented fact this architecture exists to prevent.
    """
    by_dotted: dict[str, tuple[ModuleContext, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    by_bare: dict[str, tuple[ModuleContext, ast.FunctionDef | ast.AsyncFunctionDef] | None] = {}
    for module in modules:
        for node in ast.walk(module.tree):
            if not isinstance(node, _FUNCTION_DEFS):
                continue
            entry = (module, node)
            by_dotted[_qualified_id(module, node)] = entry
            by_bare[node.name] = None if node.name in by_bare else entry
    return by_dotted, by_bare


def _lookup(
    definitions: _DefinitionIndex, module: ModuleContext, dotted: str
) -> tuple[ModuleContext, ast.FunctionDef | ast.AsyncFunctionDef] | None:
    """The repo function a resolved dotted name refers to, if any."""
    by_dotted, by_bare = definitions
    for candidate in (dotted, f"{module.module_id}.{dotted}"):
        found = by_dotted.get(candidate)
        if found is not None:
            return found
    return by_bare.get(dotted.rsplit(".", 1)[-1])


def _qualified_id(module: ModuleContext, node: ast.AST) -> str:
    """`{module}.{qualname}` -- the same dotted name the graph's node ids
    are built from, so a tool's `callable` is resolvable in the graph.
    """
    return f"{module.module_id}.{'.'.join(_qualified_name(node))}"


def _dotted_name(module: ModuleContext, expression: ast.expr) -> str | None:
    """The dotted name an expression refers to, with imports and aliases
    resolved. None when the expression is not a name at all (a lambda, a
    subscript, a call result).

    Routed through `resolve_callee_name` -- which is the only public entry
    to dataflow's alias resolution -- by wrapping the expression in a
    synthetic call, so decorator and argument references resolve by exactly
    the same rules call sites do.
    """
    return resolve_callee_name(module, ast.Call(func=expression, args=[], keywords=[]))


# --------------------------------------------------------------------------
# Deduplication
# --------------------------------------------------------------------------


def _deduplicate(found: list[ToolSite]) -> list[ToolSite]:
    """One `ToolSite` per tool, carrying every rule that found it.

    Keyed on (callable, name) rather than callable alone: two
    `Tool(name=..., func=same)` registrations are genuinely two tools as far
    as the model is concerned. The surviving record is whichever one resolved
    to a definition, so the merge never trades a full signature for an empty
    one.
    """
    merged: dict[tuple[str, str], ToolSite] = {}
    for site in found:
        key = (site.callable, site.name)
        existing = merged.get(key)
        if existing is None:
            merged[key] = site
            continue
        winner = existing if existing.resolved or not site.resolved else site
        winner.detections = existing.detections | site.detections
        winner.description = winner.description or existing.description or site.description
        merged[key] = winner

    for site in merged.values():
        site.detection = ", ".join(sorted(site.detections))
    return sorted(merged.values(), key=lambda s: (s.site.file, s.site.line, s.name))
