"""Read the agent's control-flow topology out of the source -- and say so
when it cannot be read.

The topology is what picks the DataRobot recipe framework
(`dr.scaffold.FRAMEWORKS`), replacing the old "whatever framework name we
saw in an import" guess. A repo importing `langgraph` may be a linear
chain, a router, or a 40-node state machine; only the builder calls say
which, so only the builder calls are consulted.

Detection is AST-based over the code property graph, exactly like
`probes.llm_calls`: every callee is resolved through the module's import
and assignment bindings (`graph.dataflow.resolve_callee_name`) before it is
matched, so a `StateGraph(` inside a docstring is invisible and an aliased
`from langgraph.graph import StateGraph as SG` is not.

What it reads
-------------
* **LangGraph** -- `StateGraph(...)` and the builder methods called on the
  object it is bound to: `add_node`, `add_edge`, `add_conditional_edges`,
  `set_entry_point`, `set_finish_point`, `compile`. `START`/`END` are kept
  as real nodes, because an edge into `END` is a fact about the agent.
  `kind=GRAPH`.
* **CrewAI** -- `Agent(...)`, `Task(...)`, `Crew(agents=..., tasks=...)`.
  Agents and tasks are both nodes; a task's `agent=` assignment is an edge.
  A crew's `tasks=[...]` order is an edge chain *only* when the process is
  sequential (CrewAI's default); a hierarchical crew's order is decided by
  a manager at runtime and is therefore recorded as unresolved, not
  fabricated. `kind=CREW`.
* **LlamaIndex** -- index/engine/retriever construction, chained through
  the receiver of each call (`index.as_query_engine()` is an edge from
  `index`). `kind=SEQUENTIAL`.
* **LangChain LCEL** -- `prompt | llm | parser`, whose `|` chain is a
  sequential topology written as an expression. `kind=SEQUENTIAL`.

The two honest outcomes
-----------------------
`None` means *no orchestration was found at all* -- no agent framework is
even imported. It is deliberately distinct from a finding with
`kind=UNKNOWN`, which means *there is a topology here and we could not read
it*. Collapsing the second into the first is how a migration silently ships
without the agent's control flow.

Likewise, a branch whose targets are not written at the call site is never
invented. `add_conditional_edges("researcher", route)` with no path map
routes on whatever string the router returns at runtime; reading the
router's `return` literals and calling them edges would produce a topology
that looks complete and is not. Those go to `unresolved`.

Multiple frameworks in one repo is a real thing (a LangGraph app whose
nodes each run a CrewAI crew). The most readable topology wins the
`kind`/`framework` fields and the rest are recorded in `unresolved`, so the
second framework is a line in a review rather than a silent omission.

Known gaps, all of which surface as `kind=UNKNOWN` rather than as silence:
AutoGen `GroupChat`, Haystack `Pipeline.connect`, Semantic Kernel, the
OpenAI Agents SDK, smolagents, CrewAI Flows (`@start`/`@listen`), LangGraph
subgraphs and `Send`-based fan-out, and any builder driven by a loop
(`for name in NODES: graph.add_node(name, ...)`).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from superrobot.ir.model import TopologyKind
from superrobot.pipeline.graph.builder import RepoGraph
from superrobot.pipeline.graph.dataflow import (
    ModuleContext,
    Site,
    analyze_modules,
    resolve_callee_name,
)

# Import prefix -> framework name. Matched against the longest dotted
# prefix of an import target, so `langchain_core.runnables` and
# `langchain.agents` both land on "langchain". Presence of one of these is
# what turns "no topology found" from `None` into `kind=UNKNOWN`.
_FRAMEWORK_IMPORTS: tuple[tuple[str, str], ...] = (
    ("langgraph", "langgraph"),
    ("crewai", "crewai"),
    ("llama_index", "llamaindex"),
    ("llama_deploy", "llamaindex"),
    ("langchain", "langchain"),
    ("langchain_core", "langchain"),
    ("langchain_community", "langchain"),
    ("langchain_experimental", "langchain"),
    ("autogen", "autogen"),
    ("autogen_agentchat", "autogen"),
    ("autogen_core", "autogen"),
    ("haystack", "haystack"),
    ("semantic_kernel", "semantic_kernel"),
    ("smolagents", "smolagents"),
    ("dspy", "dspy"),
    ("pydantic_ai", "pydantic_ai"),
    ("nat", "nat"),
    ("aiq", "nat"),
    ("controlflow", "controlflow"),
    ("agno", "agno"),
    ("phi", "agno"),
    ("letta", "letta"),
    ("swarm", "swarm"),
    ("atomic_agents", "atomic_agents"),
)

# Order used to break a tie between two equally-readable topologies, so the
# result of a scan does not depend on filesystem iteration order.
_FRAMEWORK_PRIORITY = ("langgraph", "crewai", "llamaindex", "langchain")

# LangGraph builder constructors. `Graph`/`MessageGraph` are the older
# spellings; all three expose the same builder methods.
_LANGGRAPH_BUILDERS = frozenset({"StateGraph", "MessageGraph", "Graph"})

# The reserved node names. A `Name` argument is only accepted as a node
# reference when it resolves to one of these -- any other variable is a
# value we cannot read, and naming a node after the variable that held it
# would be a guess.
_TERMINALS = {"START": "START", "END": "END", "__start__": "START", "__end__": "END"}

_CREWAI_AGENTS = frozenset({"Agent"})
_CREWAI_TASKS = frozenset({"Task"})
_CREWAI_CREWS = frozenset({"Crew"})

# LlamaIndex stage constructors and builder methods. Each one produces a
# pipeline stage; the receiver it is called on is the previous stage.
_LLAMAINDEX_STAGES = frozenset(
    {
        "VectorStoreIndex",
        "SummaryIndex",
        "ListIndex",
        "TreeIndex",
        "KnowledgeGraphIndex",
        "DocumentSummaryIndex",
        "PropertyGraphIndex",
        "QueryPipeline",
        "SimpleDirectoryReader",
        "from_documents",
        "from_vector_store",
        "as_query_engine",
        "as_chat_engine",
        "as_retriever",
        "get_response_synthesizer",
        "load_data",
    }
)


@dataclass
class NodeFinding:
    """One step in the topology.

    `callable` is the fully-qualified name of the function implementing the
    node when it resolves to one in this repo, otherwise the dotted name as
    written, otherwise None -- never a guess.
    """

    name: str
    kind: str
    callable: str | None
    site: Site


@dataclass
class EdgeFinding:
    """One transition. `condition` is the source text of whatever decides
    the branch (usually a router function's resolved name), None for an
    unconditional edge.
    """

    source: str
    target: str
    condition: str | None
    site: Site


@dataclass
class OrchestrationFinding:
    """The repo's control-flow topology, plus everything about it we could
    not read. `unresolved` being non-empty does not invalidate the nodes and
    edges that *are* here; it is the part a human has to look at.
    """

    kind: TopologyKind
    framework: str | None
    nodes: list[NodeFinding] = field(default_factory=list)
    edges: list[EdgeFinding] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    site: Site = field(default_factory=lambda: Site(file="", line=0, node_id=""))


def find_orchestration(repo_graph: RepoGraph) -> OrchestrationFinding | None:
    """The agent's topology, or None if the repo contains no orchestration
    at all.

    A repo that imports an agent framework always returns a finding, even
    when nothing about its topology could be read -- see the module
    docstring for why `None` and `kind=UNKNOWN` must stay distinguishable.
    """
    modules = sorted(analyze_modules(repo_graph), key=lambda module: str(module.path))
    imported = _imported_frameworks(modules)

    candidates = [
        candidate
        for build in (_langgraph_topology, _crewai_topology, _llamaindex_topology, _lcel_topology)
        for candidate in [build(modules)]
        if candidate is not None
    ]
    if not candidates:
        return _unreadable(imported)

    candidates.sort(key=_readability, reverse=True)
    best, *rest = candidates
    for other in rest:
        best.unresolved.append(
            f"{other.framework} topology at {other.site.file}:{other.site.line} "
            f"({len(other.nodes)} nodes, {len(other.edges)} edges) is also present in this "
            "repo and is not represented by this finding"
        )
    for framework, site in imported.items():
        if framework not in {candidate.framework for candidate in candidates}:
            best.unresolved.append(
                f"{framework} is imported at {site.file}:{site.line} but no topology "
                "was readable from it"
            )
    return best


def _readability(finding: OrchestrationFinding) -> tuple[int, int]:
    """How much topology a candidate actually resolved. Ties break on a
    fixed framework order so a scan is reproducible.
    """
    priority = _FRAMEWORK_PRIORITY.index(finding.framework or "")
    return (
        len(finding.nodes) + len(finding.edges),
        len(_FRAMEWORK_PRIORITY) - priority,
    )


def _unreadable(imported: dict[str, Site]) -> OrchestrationFinding | None:
    """The fallback: a framework is here but its topology is not readable."""
    if not imported:
        return None
    framework, site = next(iter(imported.items()))
    return OrchestrationFinding(
        kind=TopologyKind.UNKNOWN,
        framework=framework,
        unresolved=[
            f"{name} is imported at {where.file}:{where.line} but no topology was readable from it"
            for name, where in imported.items()
        ],
        site=site,
    )


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _imported_frameworks(modules: list[ModuleContext]) -> dict[str, Site]:
    """Every agent framework imported anywhere in the repo, with the site of
    its first import. Insertion order follows file order, so the fallback
    picks the same framework every run.
    """
    found: dict[str, Site] = {}
    for module in modules:
        for name, node in _imported_names(module):
            framework = _framework_for(name)
            if framework is not None and framework not in found:
                found[framework] = module.site_for(node)
    return found


def _imported_names(module: ModuleContext) -> list[tuple[str, ast.stmt]]:
    names: list[tuple[str, ast.stmt]] = []
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            names.extend((alias.name, node) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append((node.module, node))
    return names


def _framework_for(imported: str) -> str | None:
    segments = imported.split(".")
    prefixes = {".".join(segments[:count]) for count in range(1, len(segments) + 1)}
    for prefix, framework in _FRAMEWORK_IMPORTS:
        if prefix in prefixes:
            return framework
    return None


def _modules_importing(modules: list[ModuleContext], framework: str) -> list[ModuleContext]:
    """Only the modules that import `framework`.

    Every pattern below is gated on this. `Agent(...)` and `Task(...)` are
    names half a dozen libraries use; matching them in a module that never
    imported CrewAI would report a topology that does not exist.
    """
    return [
        module
        for module in modules
        if any(_framework_for(name) == framework for name, _ in _imported_names(module))
    ]


def _ordered_calls(module: ModuleContext) -> list[ast.Call]:
    """Every call in the module in source order. `ast.walk` yields
    breadth-first, which would scramble the order of builder calls -- and
    the order of `add_node` calls is the order the reviewer reads.
    """
    calls = [node for node in ast.walk(module.tree) if isinstance(node, ast.Call)]
    return sorted(calls, key=lambda call: (call.lineno, call.col_offset))


def _callee(module: ModuleContext, call: ast.Call) -> str | None:
    return resolve_callee_name(module, call)


def _last_segment(dotted: str | None) -> str:
    return dotted.rsplit(".", 1)[-1] if dotted else ""


def _qualified(module: ModuleContext, expr: ast.expr) -> str | None:
    """The fully-qualified name an expression refers to, if it names
    anything at all.

    A bare name defined in this repo is qualified with its module, so a node
    callable is a graph-resolvable id rather than a local name that could
    mean anything.
    """
    if not isinstance(expr, (ast.Name, ast.Attribute)):
        return None
    dotted = resolve_callee_name(module, ast.Call(func=expr, args=[], keywords=[]))
    if dotted is None:
        return None
    if "." not in dotted:
        qualified = f"{module.module_id}.{dotted}"
        if module.repo_graph.graph.has_node(qualified):
            return qualified
    return dotted


def _assigned_keys(call: ast.Call) -> list[str]:
    """The variable(s) this call's result is bound to, as written.

    Keyed by source text rather than by name so `self._graph = StateGraph()`
    and `self._graph.add_node(...)` line up without modelling attributes.
    """
    parent = getattr(call, "parent", None)
    if not isinstance(parent, ast.Assign):
        return []
    return [
        ast.unparse(target)
        for target in parent.targets
        if isinstance(target, (ast.Name, ast.Attribute))
    ]


def _receiver_key(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return ast.unparse(call.func.value)
    return None


def _positional(call: ast.Call, index: int) -> ast.expr | None:
    return call.args[index] if len(call.args) > index else None


def _keyword(call: ast.Call, *names: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg in names:
            return keyword.value
    return None


def _where(site: Site) -> str:
    return f"{site.file}:{site.line}"


# --------------------------------------------------------------------------
# LangGraph
# --------------------------------------------------------------------------


class _Topology:
    """Accumulator that keeps nodes unique and in first-seen order."""

    def __init__(self) -> None:
        self.nodes: dict[str, NodeFinding] = {}
        self.edges: list[EdgeFinding] = []
        self.unresolved: list[str] = []

    def node(self, name: str, kind: str, callable_name: str | None, site: Site) -> None:
        existing = self.nodes.get(name)
        if existing is None:
            self.nodes[name] = NodeFinding(name=name, kind=kind, callable=callable_name, site=site)
        elif existing.callable is None and callable_name is not None:
            existing.callable = callable_name

    def edge(self, source: str, target: str, condition: str | None, site: Site) -> None:
        self.edges.append(EdgeFinding(source=source, target=target, condition=condition, site=site))


def _langgraph_topology(modules: list[ModuleContext]) -> OrchestrationFinding | None:
    """Read a LangGraph `StateGraph` builder.

    Two passes: the first finds every builder object and the names it is
    bound to, the second reads the methods called on those names. Splitting
    them means a builder assigned after its first use (or in another
    function in the same module) is still recognized.
    """
    relevant = _modules_importing(modules, "langgraph")
    if not relevant:
        return None

    builders: dict[tuple[str, str], Site] = {}
    for module in relevant:
        for call in _ordered_calls(module):
            if _last_segment(_callee(module, call)) not in _LANGGRAPH_BUILDERS:
                continue
            site = module.site_for(call)
            for key in [*_assigned_keys(call), ast.unparse(call)]:
                builders.setdefault((str(module.path), key), site)
    if not builders:
        return None

    topology = _Topology()
    compiled = False
    for module in relevant:
        for call in _ordered_calls(module):
            receiver = _receiver_key(call)
            if receiver is None or (str(module.path), receiver) not in builders:
                continue
            method = call.func.attr if isinstance(call.func, ast.Attribute) else ""
            compiled |= method == "compile"
            _langgraph_method(topology, module, call, method)

    graph_sites = {(site.file, site.line) for site in builders.values()}
    if len(graph_sites) > 1:
        topology.unresolved.append(
            f"{len(graph_sites)} separate graph builders were found and merged into one "
            "topology; subgraph nesting is not modelled"
        )
    if not compiled:
        topology.unresolved.append(
            "no .compile() call was found, so which builder is the runnable topology "
            "is not stated in the source"
        )

    site = sorted(builders.values(), key=lambda s: (s.file, s.line))[0]
    return OrchestrationFinding(
        kind=TopologyKind.GRAPH,
        framework="langgraph",
        nodes=list(topology.nodes.values()),
        edges=topology.edges,
        unresolved=topology.unresolved,
        site=site,
    )


def _langgraph_method(
    topology: _Topology, module: ModuleContext, call: ast.Call, method: str
) -> None:
    site = module.site_for(call)
    if method == "add_node":
        _langgraph_add_node(topology, module, call, site)
    elif method == "add_edge":
        _langgraph_add_edge(topology, module, call, site)
    elif method == "add_conditional_edges":
        _langgraph_conditional(topology, module, call, site)
    elif method in ("set_entry_point", "set_finish_point"):
        _langgraph_terminal_edge(topology, module, call, site, method)
    elif method == "set_conditional_entry_point":
        topology.unresolved.append(
            f"conditional entry point at {_where(site)}: the entry branch is chosen at "
            "runtime and its targets are not written at the call site"
        )
    elif method == "add_sequence":
        topology.unresolved.append(
            f"add_sequence(...) at {_where(site)} is not read by this probe; its steps "
            "are missing from the topology"
        )


def _langgraph_add_node(
    topology: _Topology, module: ModuleContext, call: ast.Call, site: Site
) -> None:
    """`add_node("name", fn)`, `add_node(fn)`, or `add_node(node=fn)`."""
    name_arg = _positional(call, 0)
    action = _positional(call, 1) or _keyword(call, "action", "node")
    if action is None and name_arg is not None and not isinstance(name_arg, ast.Constant):
        # Single-argument form: the callable's own name is the node name.
        action, name_arg = name_arg, None

    callable_name = _qualified(module, action) if action is not None else None
    name = _node_name(module, name_arg) if name_arg is not None else _last_segment(callable_name)
    if not name:
        topology.unresolved.append(
            f"add_node(...) at {_where(site)}: the node name is not statically readable "
            f"({ast.unparse(call)})"
        )
        return
    if action is not None and callable_name is None:
        topology.unresolved.append(
            f"node {name!r} at {_where(site)} is implemented by an expression that names "
            f"no callable ({ast.unparse(action)})"
        )
    topology.node(name, "step", callable_name, site)


def _langgraph_add_edge(
    topology: _Topology, module: ModuleContext, call: ast.Call, site: Site
) -> None:
    sources = _node_names(module, _positional(call, 0) or _keyword(call, "start_key"))
    targets = _node_names(module, _positional(call, 1) or _keyword(call, "end_key"))
    if not sources or not targets:
        topology.unresolved.append(
            f"add_edge(...) at {_where(site)}: endpoints are not statically readable "
            f"({ast.unparse(call)})"
        )
        return
    for source in sources:
        for target in targets:
            _ensure_terminal(topology, source, site)
            _ensure_terminal(topology, target, site)
            topology.edge(source, target, None, site)


def _langgraph_conditional(
    topology: _Topology, module: ModuleContext, call: ast.Call, site: Site
) -> None:
    """`add_conditional_edges(src, router, path_map)`.

    With a path map the branch targets are written down and become real
    edges. Without one they are whatever the router returns at runtime --
    reading the router's body for `return "writer"` would produce a
    plausible, unverified topology, so instead the branch is recorded as
    unresolved.
    """
    sources = _node_names(module, _positional(call, 0) or _keyword(call, "source"))
    router = _positional(call, 1) or _keyword(call, "path", "condition")
    mapping = _positional(call, 2) or _keyword(call, "path_map", "conditional_edge_mapping")
    condition = (_qualified(module, router) if router is not None else None) or (
        ast.unparse(router) if router is not None else None
    )
    source = sources[0] if sources else None

    targets = _mapping_targets(module, mapping) if mapping is not None else None
    if source is None or targets is None:
        topology.unresolved.append(
            f"conditional edges from {source or ast.unparse(call)!r} via {condition!r} at "
            f"{_where(site)}: the branch targets are chosen at runtime and are not written "
            "at the call site (no path map), so no edges were recorded for them"
        )
        return
    for target in targets:
        _ensure_terminal(topology, target, site)
        topology.edge(source, target, condition, site)


def _langgraph_terminal_edge(
    topology: _Topology, module: ModuleContext, call: ast.Call, site: Site, method: str
) -> None:
    names = _node_names(module, _positional(call, 0) or _keyword(call, "key"))
    if not names:
        topology.unresolved.append(
            f"{method}(...) at {_where(site)}: the node is not statically readable "
            f"({ast.unparse(call)})"
        )
        return
    terminal = "START" if method == "set_entry_point" else "END"
    _ensure_terminal(topology, terminal, site)
    for name in names:
        if method == "set_entry_point":
            topology.edge(terminal, name, None, site)
        else:
            topology.edge(name, terminal, None, site)


def _ensure_terminal(topology: _Topology, name: str, site: Site) -> None:
    if name in ("START", "END"):
        topology.node(name, "terminal", None, site)


def _node_name(module: ModuleContext, expr: ast.expr | None) -> str:
    """A single node reference: a string literal, or `START`/`END`.

    Any other expression returns "" -- a variable holding a node name is a
    value this probe cannot read, and naming the node after the variable
    would invent a node that is not in the graph.
    """
    if expr is None:
        return ""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, (ast.Name, ast.Attribute)):
        dotted = resolve_callee_name(module, ast.Call(func=expr, args=[], keywords=[]))
        return _TERMINALS.get(_last_segment(dotted), "")
    return ""


def _node_names(module: ModuleContext, expr: ast.expr | None) -> list[str]:
    """Node references, expanding LangGraph's list form for fan-in edges."""
    if isinstance(expr, (ast.List, ast.Tuple)):
        names = [_node_name(module, element) for element in expr.elts]
        return [name for name in names if name]
    name = _node_name(module, expr)
    return [name] if name else []


def _mapping_targets(module: ModuleContext, mapping: ast.expr) -> list[str] | None:
    """Branch targets from a literal path map. None when the map itself is
    not a literal, which is a value we cannot read rather than an empty one.
    """
    if isinstance(mapping, ast.Dict):
        values = mapping.values
    elif isinstance(mapping, (ast.List, ast.Tuple)):
        values = list(mapping.elts)
    else:
        return None
    targets = [_node_name(module, value) for value in values]
    if any(not target for target in targets):
        return None
    return targets


# --------------------------------------------------------------------------
# CrewAI
# --------------------------------------------------------------------------


def _crewai_topology(modules: list[ModuleContext]) -> OrchestrationFinding | None:
    """Read a CrewAI crew: agents, tasks, and the assignments between them."""
    relevant = _modules_importing(modules, "crewai")
    if not relevant:
        return None

    topology = _Topology()
    #: (module path, source text of a variable) -> node name it refers to
    variables: dict[tuple[str, str], str] = {}
    #: id(call) -> node name, for an Agent/Task constructed inline
    inline: dict[int, str] = {}
    crews: list[tuple[ModuleContext, ast.Call, Site]] = []
    site: Site | None = None

    for module in relevant:
        for call in _ordered_calls(module):
            last = _last_segment(_callee(module, call))
            call_site = module.site_for(call)
            if last in _CREWAI_AGENTS:
                name = _crewai_name(call, "role", _assigned_keys(call), "agent", call_site)
                topology.node(name, "agent", _qualified(module, call.func), call_site)
            elif last in _CREWAI_TASKS:
                name = _crewai_name(call, None, _assigned_keys(call), "task", call_site)
                topology.node(name, "task", _qualified(module, call.func), call_site)
            elif last in _CREWAI_CREWS:
                crews.append((module, call, call_site))
                site = site or call_site
                continue
            else:
                continue
            inline[id(call)] = name
            for key in _assigned_keys(call):
                variables[(str(module.path), key)] = name
            site = site or call_site

    if not topology.nodes and not crews:
        return None

    for module in relevant:
        for call in _ordered_calls(module):
            if _last_segment(_callee(module, call)) in _CREWAI_TASKS:
                _crewai_task_edge(topology, module, call, variables, inline)
    for module, call, call_site in crews:
        _crewai_crew_order(topology, module, call, call_site, variables, inline)

    assert site is not None
    return OrchestrationFinding(
        kind=TopologyKind.CREW,
        framework="crewai",
        nodes=list(topology.nodes.values()),
        edges=topology.edges,
        unresolved=topology.unresolved,
        site=site,
    )


def _crewai_name(
    call: ast.Call, literal_field: str | None, assigned: list[str], prefix: str, site: Site
) -> str:
    """An agent's `role` is its identity; a task's is the variable it is
    bound to. Neither is ever invented -- the last resort names the node
    after the line it is written on, which is at least checkable.
    """
    if literal_field is not None:
        value = _keyword(call, literal_field)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    if assigned:
        return assigned[0]
    return f"{prefix}@{site.line}"


def _crewai_task_edge(
    topology: _Topology,
    module: ModuleContext,
    call: ast.Call,
    variables: dict[tuple[str, str], str],
    inline: dict[int, str],
) -> None:
    task = inline.get(id(call))
    agent = _keyword(call, "agent")
    if task is None or agent is None:
        return
    site = module.site_for(call)
    target = _crewai_reference(module, agent, variables, inline)
    if target is None:
        topology.unresolved.append(
            f"task {task!r} at {_where(site)} is assigned to an agent this probe cannot "
            f"identify ({ast.unparse(agent)})"
        )
        return
    topology.edge(task, target, None, site)


def _crewai_crew_order(
    topology: _Topology,
    module: ModuleContext,
    call: ast.Call,
    site: Site,
    variables: dict[tuple[str, str], str],
    inline: dict[int, str],
) -> None:
    """A sequential crew runs its `tasks=[...]` in the order written, which
    is a readable edge chain. A hierarchical crew's order is decided by a
    manager agent at runtime; recording an invented chain for it would be
    exactly the guess this probe exists to avoid.
    """
    process = _keyword(call, "process")
    if process is not None and "sequential" not in ast.unparse(process):
        topology.unresolved.append(
            f"crew at {_where(site)} runs process={ast.unparse(process)} (not sequential), "
            "so the task order is decided at runtime and no ordering edges were recorded"
        )
        return
    tasks = _keyword(call, "tasks")
    if not isinstance(tasks, (ast.List, ast.Tuple)):
        if tasks is not None:
            topology.unresolved.append(
                f"crew at {_where(site)} takes its tasks from {ast.unparse(tasks)}, which "
                "is not a literal list, so their order is unknown"
            )
        return
    names = [_crewai_reference(module, element, variables, inline) for element in tasks.elts]
    for element, name in zip(tasks.elts, names, strict=True):
        if name is None:
            topology.unresolved.append(
                f"crew at {_where(site)} lists a task this probe cannot identify "
                f"({ast.unparse(element)})"
            )
    ordered = [name for name in names if name is not None]
    for source, target in zip(ordered, ordered[1:], strict=False):
        topology.edge(source, target, None, site)


def _crewai_reference(
    module: ModuleContext,
    expr: ast.expr,
    variables: dict[tuple[str, str], str],
    inline: dict[int, str],
) -> str | None:
    if isinstance(expr, ast.Call):
        return inline.get(id(expr))
    if isinstance(expr, (ast.Name, ast.Attribute)):
        return variables.get((str(module.path), ast.unparse(expr)))
    return None


# --------------------------------------------------------------------------
# LlamaIndex
# --------------------------------------------------------------------------


def _llamaindex_topology(modules: list[ModuleContext]) -> OrchestrationFinding | None:
    """Read a LlamaIndex pipeline as the chain of stages it is built from.

    The edges come from the receiver of each call: `index.as_query_engine()`
    is a stage whose input is `index`. That is a real dataflow relation
    written in the source, not an ordering assumed from line numbers.
    """
    relevant = _modules_importing(modules, "llamaindex")
    if not relevant:
        return None

    topology = _Topology()
    stages: dict[tuple[str, str], str] = {}
    site: Site | None = None

    for module in relevant:
        for call in _ordered_calls(module):
            callee = _callee(module, call)
            if _last_segment(callee) not in _LLAMAINDEX_STAGES:
                continue
            call_site = module.site_for(call)
            site = site or call_site
            assigned = _assigned_keys(call)
            name = assigned[0] if assigned else f"{_last_segment(callee)}@{call_site.line}"
            topology.node(name, "stage", callee, call_site)
            for key in assigned:
                stages[(str(module.path), key)] = name
            receiver = _receiver_key(call)
            previous = stages.get((str(module.path), receiver)) if receiver else None
            if previous is not None and previous != name:
                topology.edge(previous, name, None, call_site)
            if _last_segment(callee) == "QueryPipeline":
                _llamaindex_pipeline(topology, module, call, name, call_site)

    if site is None:
        return None
    return OrchestrationFinding(
        kind=TopologyKind.SEQUENTIAL,
        framework="llamaindex",
        nodes=list(topology.nodes.values()),
        edges=topology.edges,
        unresolved=topology.unresolved,
        site=site,
    )


def _llamaindex_pipeline(
    topology: _Topology, module: ModuleContext, call: ast.Call, name: str, site: Site
) -> None:
    """`QueryPipeline(chain=[a, b, c])` states its own order."""
    chain = _keyword(call, "chain")
    if not isinstance(chain, (ast.List, ast.Tuple)):
        topology.unresolved.append(
            f"QueryPipeline at {_where(site)} does not declare a literal chain=[...]; its "
            "internal links (add_modules/add_link) are not read by this probe"
        )
        return
    previous = name
    for element in chain.elts:
        step = _qualified(module, element) or ast.unparse(element)
        topology.node(step, "stage", _qualified(module, element), site)
        topology.edge(previous, step, None, site)
        previous = step


# --------------------------------------------------------------------------
# LangChain LCEL
# --------------------------------------------------------------------------


def _lcel_topology(modules: list[ModuleContext]) -> OrchestrationFinding | None:
    """Read an LCEL chain (`prompt | llm | parser`) as a sequential topology.

    Only ever consulted in a module that imports LangChain, since `|` is
    also just bitwise-or.
    """
    relevant = _modules_importing(modules, "langchain")
    if not relevant:
        return None

    topology = _Topology()
    site: Site | None = None
    for module in relevant:
        for node in ast.walk(module.tree):
            if not _is_pipe(node) or _is_pipe(getattr(node, "parent", None)):
                continue
            assert isinstance(node, ast.BinOp)
            operands = _pipe_operands(node)
            if len(operands) < 2:
                continue
            chain_site = module.site_for(node)
            site = site or chain_site
            names = []
            for operand in operands:
                name = ast.unparse(operand)
                topology.node(name, "step", _qualified(module, operand), chain_site)
                names.append(name)
            for source, target in zip(names, names[1:], strict=False):
                topology.edge(source, target, None, chain_site)

    if site is None:
        return None
    return OrchestrationFinding(
        kind=TopologyKind.SEQUENTIAL,
        framework="langchain",
        nodes=list(topology.nodes.values()),
        edges=topology.edges,
        unresolved=topology.unresolved,
        site=site,
    )


def _is_pipe(node: ast.AST | None) -> bool:
    return isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)


def _pipe_operands(node: ast.BinOp) -> list[ast.expr]:
    left = (
        _pipe_operands(node.left)
        if isinstance(node.left, ast.BinOp) and _is_pipe(node.left)
        else [node.left]
    )
    right = (
        _pipe_operands(node.right)
        if isinstance(node.right, ast.BinOp) and _is_pipe(node.right)
        else [node.right]
    )
    return [*left, *right]
