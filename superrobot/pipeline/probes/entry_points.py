"""Every plausible entry point of a repo, as a structured fact with
provenance -- not just the single winner.

`superrobot.pipeline.graph.entry_points.resolve_entry_point` answers "which
one node is the entry point?" and is the right shape for the reachability
layer, which needs exactly one root. It is the wrong shape for the IR: a
migration that picks the wrong entry point produces an agent with the wrong
*interface*, and that is not a defect a reviewer can spot from a single
dotted name. So this probe runs the same three resolution tiers (console
script -> `__main__` guard -> name/filename heuristic, all imported from
that module rather than restated here, so the two can never disagree about
what a candidate is) and reports every candidate each tier finds, with the
tier recorded in `confidence` and the full signature attached.

Reported in tier order: `console_script` first (a declared console script is
what the package literally installs), then `main_guard`, then `heuristic`
ranked by scanner.py's own scoring. A function found by more than one tier
is reported once, under the strongest tier that found it.

What it does NOT find
---------------------
* an entry point reached only through dynamic dispatch -- a framework
  loading `getattr(module, name)`, a plugin registry, a `Makefile`/Procfile
  `python -c` incantation. Nothing in the AST names it.
* the *server* entry points of a repo whose real interface is HTTP: a
  FastAPI `@app.post("/chat")` handler is an entry point in every sense that
  matters, but it is named by a decorator, not by the tiers above, and only
  turns up here if it happens to be called `chat`/`run`/`invoke`.
* an entry point declared in `setup.py`/`setup.cfg` entry_points rather than
  `pyproject.toml` `[project.scripts]`.

An empty list means no tier resolved anything. That is a fact for the
caller to block on, not a signal that the repo has no interface.
"""

from __future__ import annotations

import ast
import tomllib
from dataclasses import dataclass, field

from superrobot.pipeline.graph.builder import (
    RepoGraph,
    _qualified_name,
    code_object_node_id,
    strip_collision_suffix,
)
from superrobot.pipeline.graph.dataflow import ModuleContext, Site, analyze_modules

# Imported, never restated: `entry_points._is_main_guard` and the heuristic's
# scoring constants are the definition of what those tiers consider a
# candidate. Re-implementing them here is how the reachability layer and the
# IR would come to disagree about the same repo.
from superrobot.pipeline.graph.entry_points import (
    _ENTRY_FILENAME_BONUS,
    _ENTRY_FILENAME_BONUS_PATHS,
    _RUN_PREFIX_BONUS,
    _is_main_guard,
    _relative_path,
)
from superrobot.pipeline.scanner import ENTRY_POINT_NAMES, ENTRY_PRIORITY

CONSOLE_SCRIPT = "console_script"
MAIN_GUARD = "main_guard"
HEURISTIC = "heuristic"

#: Strongest first. A function found by several tiers keeps the strongest.
_TIER_ORDER = (CONSOLE_SCRIPT, MAIN_GUARD, HEURISTIC)

_FunctionDef = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass
class ParamSite:
    """One parameter of an entry point.

    `annotation` and `default` are the source text as written (via
    `ast.unparse`, so normalized but faithful) rather than an interpretation
    of them: the IR's job is to carry what the source says, and a default
    like `os.getenv("MODE")` has no value to evaluate. `name` carries the
    `*`/`**` marker for variadic parameters so a caller cannot mistake them
    for positional ones.
    """

    name: str
    annotation: str | None = None
    default: str | None = None


@dataclass
class EntryPointSite:
    """One callable that plausibly is the repo's interface.

    `function` is qualified within its module (`Agent.run` for a method), so
    it can be joined back to `site.node_id` -- which is the graph node id,
    byte-identical to the one the graph pass assigned.
    """

    module: str
    function: str
    signature: str
    is_async: bool
    parameters: list[ParamSite]
    returns: str | None
    docstring: str | None
    confidence: str
    site: Site
    score: int = field(default=0, compare=False)
    """The heuristic rank (scanner.py's scoring) -- 0 for the other tiers,
    which do not rank. Kept for ordering; not a probability."""


def find_entry_points(repo_graph: RepoGraph) -> list[EntryPointSite]:
    """Every plausible entry point, strongest tier first.

    Empty when no tier resolves anything, which the caller should treat as
    blocking rather than as "this repo has no entry point".
    """
    definitions = _function_definitions(repo_graph)

    found: dict[str, tuple[str, int]] = {}
    for tier, node_id, score in [
        *((CONSOLE_SCRIPT, node_id, 0) for node_id in _console_scripts(repo_graph)),
        *((MAIN_GUARD, node_id, 0) for node_id in _main_guard_targets(repo_graph)),
        *((HEURISTIC, node_id, score) for node_id, score in _heuristic_candidates(repo_graph)),
    ]:
        if node_id not in definitions or node_id in found:
            continue
        found[node_id] = (tier, score)

    sites = [
        _entry_point_site(definitions[node_id], node_id, tier, score)
        for node_id, (tier, score) in found.items()
    ]
    return sorted(
        sites,
        key=lambda s: (_TIER_ORDER.index(s.confidence), -s.score, s.site.file, s.site.line),
    )


# --------------------------------------------------------------------------
# The three tiers, each yielding *every* candidate rather than the first
# --------------------------------------------------------------------------


def _console_scripts(repo_graph: RepoGraph) -> list[str]:
    """Every `[project.scripts]` target that resolves into the graph.

    Routed through `code_object_node_id` for the same reason
    `graph.entry_points` does: the bare `module.func` dotted name can collide
    with a real module's own name, in which case the function lives at the
    disambiguated id.
    """
    pyproject_path = repo_graph.repo_root / "pyproject.toml"
    if not pyproject_path.is_file():
        return []
    try:
        data = tomllib.loads(pyproject_path.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return []

    targets: list[str] = []
    scripts = data.get("project", {}).get("scripts", {})
    if not isinstance(scripts, dict):
        return []
    for target in scripts.values():
        if not isinstance(target, str):
            continue
        module_part, _, func_part = target.partition(":")
        if not func_part:
            continue
        candidate = code_object_node_id(f"{module_part}.{func_part}", repo_graph.graph)
        if candidate in repo_graph.graph:
            targets.append(candidate)
    return targets


def _main_guard_targets(repo_graph: RepoGraph) -> list[str]:
    """Every function called from an `if __name__ == "__main__":` guard.

    Same guard test and same "call by bare name" restriction as
    `graph.entry_points._resolve_main_guard_call`; a guard that calls
    `asyncio.run(run_agent())` contributes `run_agent`, since that inner call
    is also a bare name inside the guard.
    """
    targets: list[str] = []
    for module in analyze_modules(repo_graph):
        for node in ast.walk(module.tree):
            if not (isinstance(node, ast.If) and _is_main_guard(node)):
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                    candidate = f"{module.module_id}.{call.func.id}"
                    if candidate in repo_graph.graph:
                        targets.append(candidate)
    return targets


def _heuristic_candidates(repo_graph: RepoGraph) -> list[tuple[str, int]]:
    """Every function node whose name looks like an entry point, scored the
    way `scanner.py` scores them, highest first.

    Mirrors `graph.entry_points._resolve_by_heuristic` exactly, except that
    it returns the whole ranking instead of `min()` of it.
    """
    candidates: list[tuple[str, int]] = []
    for node_id, attrs in repo_graph.graph.nodes(data=True):
        if attrs.get("kind") != "function":
            continue
        local_name = strip_collision_suffix(node_id).rsplit(".", 1)[-1]
        if local_name not in ENTRY_POINT_NAMES and not local_name.startswith("run_"):
            continue

        score = ENTRY_PRIORITY.get(local_name, 0)
        if _relative_path(repo_graph, attrs.get("path")) in _ENTRY_FILENAME_BONUS_PATHS:
            score += _ENTRY_FILENAME_BONUS
        if local_name.startswith("run_"):
            score += _RUN_PREFIX_BONUS
        candidates.append((node_id, score))

    # Ties break on the node id, never on graph iteration order, which
    # follows filesystem enumeration and so differs between machines.
    return sorted(candidates, key=lambda candidate: (-candidate[1], candidate[0]))


# --------------------------------------------------------------------------
# From a graph node id back to the definition that produced it
# --------------------------------------------------------------------------


def _function_definitions(
    repo_graph: RepoGraph,
) -> dict[str, tuple[ModuleContext, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Graph node id -> the AST definition it was built from.

    Keyed by `code_object_node_id` so the key is the id the graph itself
    uses, collision suffix and all -- looking a candidate up by any other
    spelling would silently miss the disambiguated ones.
    """
    definitions: dict[str, tuple[ModuleContext, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for module in analyze_modules(repo_graph):
        for node in ast.walk(module.tree):
            if not isinstance(node, _FunctionDef):
                continue
            dotted = f"{module.module_id}.{'.'.join(_qualified_name(node))}"
            definitions.setdefault(code_object_node_id(dotted, repo_graph.graph), (module, node))
    return definitions


def _entry_point_site(
    definition: tuple[ModuleContext, ast.FunctionDef | ast.AsyncFunctionDef],
    node_id: str,
    confidence: str,
    score: int,
) -> EntryPointSite:
    module, node = definition
    parameters = _parameters(node)
    returns = ast.unparse(node.returns) if node.returns is not None else None
    return EntryPointSite(
        module=module.module_id,
        function=".".join(_qualified_name(node)),
        signature=_signature(parameters, returns),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        parameters=parameters,
        returns=returns,
        docstring=ast.get_docstring(node),
        confidence=confidence,
        site=module.site_for(node),
        score=score,
    )


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ParamSite]:
    """Every parameter in call order, defaults matched to their own argument.

    Positional defaults bind to the *last* n positional parameters, which is
    why they are zipped from the right; getting this wrong would attach
    `max_sources`' default to `query`.
    """
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    padding: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    positional_defaults: list[ast.expr | None] = [*padding, *args.defaults]

    parameters = [
        _param(argument, default)
        for argument, default in zip(positional, positional_defaults, strict=True)
    ]
    if args.vararg is not None:
        parameters.append(_param(args.vararg, None, prefix="*"))
    parameters.extend(
        _param(argument, default)
        for argument, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
    )
    if args.kwarg is not None:
        parameters.append(_param(args.kwarg, None, prefix="**"))
    return parameters


def _param(argument: ast.arg, default: ast.expr | None, prefix: str = "") -> ParamSite:
    return ParamSite(
        name=f"{prefix}{argument.arg}",
        annotation=ast.unparse(argument.annotation) if argument.annotation is not None else None,
        default=ast.unparse(default) if default is not None else None,
    )


def _signature(parameters: list[ParamSite], returns: str | None) -> str:
    """The signature as a human reads it -- PEP 8 spacing, so an annotated
    default is `x: int = 3` and an unannotated one is `x=3`.
    """
    rendered: list[str] = []
    for parameter in parameters:
        text = parameter.name
        if parameter.annotation is not None:
            text += f": {parameter.annotation}"
        if parameter.default is not None:
            text += f" = {parameter.default}" if parameter.annotation else f"={parameter.default}"
        rendered.append(text)
    suffix = f" -> {returns}" if returns is not None else ""
    return f"({', '.join(rendered)}){suffix}"
