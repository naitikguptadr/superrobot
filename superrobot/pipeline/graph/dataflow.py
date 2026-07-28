"""Intraprocedural reaching-definitions over the repo's ASTs: what values
actually flow into a given parameter of a given callable?

This is the deterministic answer to the question the previous, regex-based
migrator could only guess at. Given

    CLS = ChatOpenAI
    name = "gpt-4o"
    llm = CLS(model=name)

`resolve_parameter_values(graph, "ChatOpenAI", "model")` reports `"gpt-4o"`,
because it resolves the *callable* through its alias chain and the
*argument* through its reaching definition, rather than matching the text
`ChatOpenAI(` and reading the characters after it.

What it resolves
----------------
* literal constants (`model="gpt-4o"`)
* single-assignment local/module variables (`name = "gpt-4o"; f(model=name)`)
* alias chains for the callable itself, through
  - `import langchain_openai as lo` + `lo.ChatOpenAI(...)`
  - `from langchain_openai import ChatOpenAI as CO` + `CO(...)`
  - plain rebinding, `CLS = ChatOpenAI` / `CLS = lo.ChatOpenAI`, transitively

What it deliberately does NOT resolve
-------------------------------------
Anything requiring interprocedural analysis (a value arriving as a function
parameter), values read at runtime (`os.environ[...]`, `cfg["model"]`),
computed values (f-strings, concatenation, calls), and names with more than
one reaching definition. Per the spec's non-goals, the analysis is
intraprocedural.

Crucially, none of those are *dropped*. Every value that cannot be resolved
becomes an `Unresolved` carrying the source expression, its provenance, and
the reason -- a first-class output, not a fallback. Silently omitting a
value we cannot resolve is precisely the failure mode this architecture
exists to prevent: an unknown model name must be escalated, never guessed
and never quietly ignored.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from superrobot.pipeline.graph.builder import (
    RepoGraph,
    _assign_parents,
    _qualified_name,
    _walk_own_body,
    code_object_node_id,
    module_dotted_name,
    parse_python_modules,
)

# Scope-defining statements. A name bound inside one of these is invisible
# to the enclosing scope, so reaching-definition search must stop here.
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# Literal types we are willing to report as a resolved value. Deliberately
# excludes None: `model=None` is a real "no model chosen here" signal, and
# rendering it as the string "None" would be indistinguishable from a model
# literally named that.
_LITERALS = (str, int, float, bool)


@dataclass(frozen=True)
class Site:
    """Where a fact came from. Every fact this module emits carries one."""

    file: str
    line: int
    node_id: str


@dataclass(frozen=True)
class Unresolved:
    """A value that reaches the parameter but cannot be known statically.

    `expression` is the source text of what was written (via `ast.unparse`,
    so it is normalized but faithful), `reason` says why static analysis
    stopped. This is what Layer 3/the coverage ledger escalates.
    """

    expression: str
    site: Site
    reason: str


@dataclass
class ParameterValues:
    """Everything that reaches a parameter: what we know, and what we don't.

    `sites` records every call site examined for the parameter, so a caller
    can report provenance even when nothing resolved.
    """

    resolved: list[str] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    sites: list[Site] = field(default_factory=list)

    def extend(self, other: ParameterValues) -> None:
        self.resolved.extend(other.resolved)
        self.unresolved.extend(other.unresolved)
        self.sites.extend(other.sites)


@dataclass
class ModuleContext:
    """One parsed module plus everything needed to reason about names in it.

    Built once per module by `analyze_modules` and reused by every probe, so
    a repo is parsed once per analysis rather than once per question asked
    of it.
    """

    path: Path
    module_id: str
    tree: ast.Module
    repo_graph: RepoGraph
    #: name bound in this module -> the dotted name it ultimately refers to
    #: (e.g. "CLS" -> "langchain_openai.ChatOpenAI"). See `_build_bindings`.
    aliases: dict[str, str]

    def site_for(self, node: ast.AST) -> Site:
        """Provenance for `node`: file, line, and the graph node id of the
        innermost function/class enclosing it (the module itself when the
        node sits at module level).
        """
        return Site(
            file=str(self.path),
            line=getattr(node, "lineno", 0),
            node_id=enclosing_node_id(self, node),
        )


def analyze_modules(repo_graph: RepoGraph) -> list[ModuleContext]:
    """Parse and index every module in the repo the graph was built from.

    Reuses `builder.parse_python_modules` so the corpus is exactly the one
    the graph itself was built from -- an analysis that disagreed with the
    graph about which files exist would produce facts with node ids that
    aren't in the graph.
    """
    contexts: list[ModuleContext] = []
    for path, tree in parse_python_modules(repo_graph.repo_root).items():
        _assign_parents(tree)
        contexts.append(
            ModuleContext(
                path=path,
                module_id=module_dotted_name(path, repo_graph.repo_root),
                tree=tree,
                repo_graph=repo_graph,
                aliases=_build_aliases(tree),
            )
        )
    return contexts


def enclosing_node_id(module: ModuleContext, node: ast.AST) -> str:
    """The graph node id of the innermost function/class containing `node`.

    Routed through `builder.code_object_node_id` so the id is byte-identical
    to the one the graph pass assigned, collision suffix and all -- a fact
    whose node id doesn't resolve in the graph is unverifiable, which
    defeats the point of carrying provenance.
    """
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, _SCOPES):
            dotted = f"{module.module_id}.{'.'.join(_qualified_name(current))}"
            return code_object_node_id(dotted, module.repo_graph.graph)
        current = getattr(current, "parent", None)
    return module.module_id


# --------------------------------------------------------------------------
# Alias resolution: what dotted name does a called expression really name?
# --------------------------------------------------------------------------


def _build_aliases(tree: ast.Module) -> dict[str, str]:
    """Map every name bound by an import or a name-to-name assignment to the
    dotted name it ultimately refers to.

    Imports first, then assignment rebinding resolved transitively against
    them, so `import langchain_openai as lo` + `CLS = lo.ChatOpenAI` yields
    `CLS -> langchain_openai.ChatOpenAI` regardless of statement order.

    A name assigned more than once from *different* sources is dropped
    entirely: we genuinely do not know which definition reaches a given use,
    and inventing one would be a guess. The call site then resolves to its
    written name and is reported on its own terms.
    """
    bindings: dict[str, ast.expr | str] = {}
    conflicted: set[str] = set()

    def bind(name: str, value: ast.expr | str) -> None:
        existing = bindings.get(name)
        if existing is not None and _binding_key(existing) != _binding_key(value):
            conflicted.add(name)
        bindings[name] = value

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # `import a.b.c` binds "a"; `import a.b.c as y` binds "y" to
                # the full dotted path.
                bind(alias.asname or alias.name.split(".")[0], alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or node.level:
                # Relative imports resolve against the package, which the
                # graph already models; for name resolution the local name
                # is the best available handle.
                continue
            for alias in node.names:
                bind(alias.asname or alias.name, f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Assign):
            if not isinstance(node.value, (ast.Name, ast.Attribute)):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bind(target.id, node.value)

    aliases: dict[str, str] = {}
    for name in bindings:
        if name in conflicted:
            continue
        resolved = _resolve_binding(name, bindings, seen=set())
        if resolved is not None and resolved != name:
            aliases[name] = resolved
    return aliases


def _binding_key(value: ast.expr | str) -> str:
    return value if isinstance(value, str) else ast.unparse(value)


def _resolve_binding(
    name: str, bindings: Mapping[str, ast.expr | str], seen: set[str]
) -> str | None:
    """Follow `name` through the binding map to a dotted name, guarding
    against cycles (`a = b; b = a` is legal Python and must not hang).
    """
    if name in seen:
        return None
    seen.add(name)
    value = bindings.get(name)
    if value is None:
        return name
    if isinstance(value, str):
        return value
    return _dotted_from_expr(value, bindings, seen)


def _dotted_from_expr(
    expr: ast.expr, bindings: Mapping[str, ast.expr | str], seen: set[str]
) -> str | None:
    """Dotted name for a Name/Attribute expression, resolving each segment
    through the binding map (so `lo.ChatOpenAI` becomes
    `langchain_openai.ChatOpenAI`).
    """
    if isinstance(expr, ast.Name):
        return _resolve_binding(expr.id, bindings, seen)
    if isinstance(expr, ast.Attribute):
        base = _dotted_from_expr(expr.value, bindings, seen)
        return f"{base}.{expr.attr}" if base else expr.attr
    return None


def resolve_callee_name(module: ModuleContext, call: ast.Call) -> str | None:
    """The dotted name a call actually targets, with aliases resolved.

    `ChatOpenAI(...)` -> "ChatOpenAI" (or its fully-qualified import path if
    imported), `lo.ChatOpenAI(...)` -> "langchain_openai.ChatOpenAI",
    `CLS(...)` where `CLS = ChatOpenAI` -> "ChatOpenAI". Returns None for
    calls whose target is not a name at all (`get_client()(...)`,
    subscripts, lambdas) -- those are reported by callers as unresolved
    rather than silently matched or silently skipped.
    """
    return _dotted_from_expr(call.func, module.aliases, seen=set())


def callee_matches(callee: str | None, callable_name: str) -> bool:
    """True if a resolved callee refers to `callable_name`.

    Matches the fully-qualified form and the bare local name, so a caller
    can ask for "ChatOpenAI" and get `langchain_openai.chat_models.ChatOpenAI`
    too.
    """
    if callee is None:
        return False
    return callee == callable_name or callee.rsplit(".", 1)[-1] == callable_name.rsplit(".", 1)[-1]


# --------------------------------------------------------------------------
# Reaching definitions for an argument expression
# --------------------------------------------------------------------------


def resolve_call_parameter(
    module: ModuleContext, call: ast.Call, parameter: str
) -> ParameterValues:
    """Resolve the value passed as keyword `parameter` at one call site.

    Positional arguments are not resolved: without the callee's signature
    (which for third-party SDK constructors is not in the repo) mapping
    position to name would be a guess. A call that passes the parameter
    positionally, or through `**kwargs`, is reported as unresolved so the
    ledger still sees it.
    """
    values = ParameterValues(sites=[module.site_for(call)])

    for keyword in call.keywords:
        if keyword.arg == parameter:
            resolved, unresolved = _resolve_expr(module, keyword.value, call)
            values.resolved.extend(resolved)
            values.unresolved.extend(unresolved)
            return values

    if any(keyword.arg is None for keyword in call.keywords):
        values.unresolved.append(
            Unresolved(
                expression=ast.unparse(call),
                site=module.site_for(call),
                reason=f"{parameter!r} may be supplied through ** unpacking",
            )
        )
    return values


def resolve_parameter_values(
    repo_graph: RepoGraph, callable_name: str, parameter: str
) -> ParameterValues:
    """Every value that reaches `parameter` of `callable_name` in the repo.

    `callable_name` may be a bare name ("ChatOpenAI") or a dotted path; call
    sites are matched after alias resolution, so aliased imports, aliased
    modules, and rebound classes all match.
    """
    values = ParameterValues()
    for module in analyze_modules(repo_graph):
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Call):
                continue
            if not callee_matches(resolve_callee_name(module, node), callable_name):
                continue
            values.extend(resolve_call_parameter(module, node, parameter))
    return values


def _resolve_expr(
    module: ModuleContext,
    expr: ast.expr,
    use: ast.AST,
    seen_names: frozenset[str] = frozenset(),
) -> tuple[list[str], list[Unresolved]]:
    """Resolve one expression to concrete values, or explain why not."""
    if isinstance(expr, ast.Constant):
        if isinstance(expr.value, _LITERALS):
            return [expr.value if isinstance(expr.value, str) else str(expr.value)], []
        return [], [_unresolvable(module, expr, "literal is not a string or number")]

    if isinstance(expr, ast.Name):
        return _resolve_name(module, expr, use, seen_names)

    return [], [_unresolvable(module, expr, f"{type(expr).__name__} is not statically known")]


def _resolve_name(
    module: ModuleContext,
    expr: ast.Name,
    use: ast.AST,
    seen_names: frozenset[str],
) -> tuple[list[str], list[Unresolved]]:
    """Resolve a variable read by finding the definition that reaches it."""
    if expr.id in seen_names:
        return [], [_unresolvable(module, expr, "circular assignment")]

    for scope in _enclosing_scopes(module, use):
        if _is_parameter(scope, expr.id):
            return [], [
                _unresolvable(
                    module, expr, "value arrives as a function parameter (interprocedural)"
                )
            ]
        definitions = _definitions_in_scope(scope, expr.id)
        if not definitions:
            continue
        if len(definitions) > 1:
            return [], [
                _unresolvable(module, expr, f"{len(definitions)} assignments reach this use")
            ]
        definition = definitions[0]
        if definition is None:
            return [], [_unresolvable(module, expr, "name is rebound by a non-assignment")]
        return _resolve_expr(module, definition, use, seen_names | {expr.id})

    return [], [_unresolvable(module, expr, "no definition found in this module")]


def _enclosing_scopes(module: ModuleContext, node: ast.AST) -> list[ast.AST]:
    """The scopes to search for a definition, innermost first.

    The chain stops at the module: reading a module-level constant from
    inside a function is a global *read*, not interprocedural dataflow, and
    it is how agent repos overwhelmingly express `MODEL = "gpt-4o"`.
    """
    scopes: list[ast.AST] = []
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append(current)
        current = getattr(current, "parent", None)
    scopes.append(module.tree)
    return scopes


def _is_parameter(scope: ast.AST, name: str) -> bool:
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    args = scope.args
    return any(
        arg.arg == name
        for arg in [
            *args.posonlyargs,
            *args.args,
            *args.kwonlyargs,
            *([args.vararg] if args.vararg else []),
            *([args.kwarg] if args.kwarg else []),
        ]
    )


def _definitions_in_scope(scope: ast.AST, name: str) -> list[ast.expr | None]:
    """Every binding of `name` in `scope`, not descending into nested scopes.

    A binding we cannot follow to an expression (a `for` target, a `with`
    target, an augmented assignment) is recorded as `None` rather than
    omitted -- it still competes with the real assignment, and pretending it
    doesn't exist would let us report a value that never reaches the use.
    """
    definitions: list[ast.expr | None] = []
    for node in _walk_own_body(scope):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            # A None value is a bare annotation (`model: str`), which binds
            # nothing at runtime.
            if node.value is not None and any(
                isinstance(target, ast.Name) and target.id == name for target in targets
            ):
                definitions.append(node.value)
        elif isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor, ast.withitem)) and _binds_name(
            node, name
        ):
            definitions.append(None)
    return definitions


def _binds_name(node: ast.AST, name: str) -> bool:
    target = getattr(node, "target", None) or getattr(node, "optional_vars", None)
    if target is None:
        return False
    return any(
        isinstance(sub, ast.Name) and sub.id == name and isinstance(sub.ctx, ast.Store)
        for sub in ast.walk(target)
    )


def _unresolvable(module: ModuleContext, expr: ast.expr, reason: str) -> Unresolved:
    return Unresolved(
        expression=ast.unparse(expr),
        site=module.site_for(expr),
        reason=reason,
    )
