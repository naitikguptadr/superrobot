"""Every environment-configuration read in a repo, and -- the reason this
probe exists -- which callable each one reaches.

An env var on its own is not a migratable fact. `OPENAI_API_KEY` reaching a
`ChatOpenAI(...)` constructor is: the target platform supplies model
credentials through DataRobot's LLM Gateway, so that read must be rewritten,
while `LOG_LEVEL` reaching a logging call must be carried across untouched.
Telling those apart is a dataflow question, not a naming question, so
`consumers` is answered by tracing the value forward through the CPG rather
than by pattern-matching the variable's name.

Detection is AST-only and resolves through import aliases, so
`from os import getenv`, `import os as _os`, and `from os import environ`
all land on the same canonical `os.getenv` / `os.environ` access. The name
inside a string literal is never a read.

What is reported
----------------
* `os.environ["X"]` -- `required=True`, there is no fallback
* `os.environ.get("X", d)` / `os.getenv("X", d)` -- required only when no
  default is written
* `pydantic_settings.BaseSettings` subclass fields, under the env var name
  pydantic-settings would actually look up (upper-cased, `env_prefix`
  applied, `alias`/`validation_alias` honored)
* `dotenv.get_key(path, "X")` and `dotenv_values(...)["X"]`
* a read whose *name* is not a literal (`os.getenv(var_name)`), under the
  source expression wrapped in `<unresolved: ...>`. Dropping it would hide
  a whole configuration surface -- a repo that reads its config through a
  loop would come out with no configuration at all.

Nothing is deduplicated: the same variable read in three places is three
facts with three `Site`s, because the migration has to rewrite all three.

What it does NOT find
---------------------
* `load_dotenv()` itself. It names no variable -- it populates
  `os.environ` from a file, and the variables actually *used* still surface
  as `os.environ` reads. The `.env` file's own contents are not read here
  (parsing it is not AST analysis, and its keys are often a superset of
  what the code uses).
* config arriving from a file the repo reads at runtime (`yaml.safe_load`,
  `json.load`, `configparser`) or from a framework's own settings object
  (LlamaIndex `Settings`, Django). Those are configuration, but not
  *environment* configuration, and each needs its own probe.
* `os.environ.setdefault(...)` / assignment into `os.environ`, which is a
  write, not a read.
* a consumer reached interprocedurally -- a key passed into a helper
  function and used there is attributed to the helper call, not to whatever
  the helper does with it. Per the spec, the dataflow layer is
  intraprocedural; the consumer list is therefore a floor, not a ceiling.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from superrobot.pipeline.graph.builder import RepoGraph
from superrobot.pipeline.graph.dataflow import (
    ModuleContext,
    Site,
    analyze_modules,
    resolve_callee_name,
)

ENVIRON_SUBSCRIPT = "os.environ[]"
ENVIRON_GET = "os.environ.get"
GETENV = "os.getenv"
DOTENV = "dotenv"
PYDANTIC_SETTINGS = "pydantic_settings"

#: Callables that read one variable out of the environment, mapped to the
#: access label reported for them. Resolved names, so aliases are already
#: handled by the time we look here.
_GETTERS = {
    "os.environ.get": ENVIRON_GET,
    "os.getenv": GETENV,
    "os.environb.get": ENVIRON_GET,
    "os.getenvb": GETENV,
}

#: Names that, once alias-resolved, refer to the environment mapping itself.
_ENVIRON_NAMES = frozenset({"os.environ", "os.environb"})

#: `dotenv.get_key(dotenv_path, "KEY")` -- the key is the second argument.
_DOTENV_GET_KEY = frozenset({"dotenv.get_key", "dotenv.main.get_key"})
_DOTENV_VALUES = frozenset({"dotenv.dotenv_values", "dotenv.main.dotenv_values"})

#: A class deriving from any of these is a settings model. Matched on the
#: last segment so `pydantic_settings.BaseSettings`, a re-export, and the
#: legacy `pydantic.BaseSettings` all qualify.
_SETTINGS_BASES = frozenset({"BaseSettings"})

#: Class attributes of a settings model that configure it rather than
#: declare a variable.
_SETTINGS_NON_FIELDS = frozenset({"model_config", "Config", "model_fields"})

#: How deep a value is followed through plain `b = a` rebinding when looking
#: for consumers. Three hops covers real code; the walk also stops early
#: once the name set stops growing, so a cycle (`a = b; b = a`) terminates.
_REBIND_DEPTH = 3


@dataclass
class ConfigSite:
    """One environment read, with everywhere the value visibly goes.

    `default` is the source text of the fallback expression, not its value:
    `os.getenv("PORT", str(DEFAULT_PORT))` has a default that cannot be
    evaluated, and rendering it as `None` would claim there is none.

    `consumers` is empty when the value could not be traced. That is a
    reportable fact -- an untraceable credential still has to be migrated --
    and never a reason to omit the site.
    """

    name: str
    access: str
    required: bool
    default: str | None
    consumers: list[str]
    site: Site
    unresolved_name: bool = field(default=False)
    """True when `name` is a source expression rather than a literal env var
    name; `name` is then wrapped in `<unresolved: ...>`."""


def find_config_sites(repo_graph: RepoGraph) -> list[ConfigSite]:
    """Every environment read in the repo, in file/line order."""
    sites: list[ConfigSite] = []
    for module in analyze_modules(repo_graph):
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Call):
                site = _call_site(module, node)
                if site is not None:
                    sites.append(site)
            elif isinstance(node, ast.Subscript):
                site = _subscript_site(module, node)
                if site is not None:
                    sites.append(site)
            elif isinstance(node, ast.ClassDef):
                sites.extend(_settings_sites(module, node))
    return sorted(sites, key=lambda s: (s.site.file, s.site.line, s.name))


# --------------------------------------------------------------------------
# The four access shapes
# --------------------------------------------------------------------------


def _call_site(module: ModuleContext, call: ast.Call) -> ConfigSite | None:
    """`os.getenv(...)`, `os.environ.get(...)`, `dotenv.get_key(...)`."""
    callee = resolve_callee_name(module, call)
    if callee is None:
        return None

    if callee in _DOTENV_GET_KEY:
        # get_key(dotenv_path, key) -- the variable is the second argument.
        if len(call.args) < 2:
            return None
        return _site(module, call, call.args[1], DOTENV, default=None, required=True)

    access = _GETTERS.get(callee)
    if access is None:
        return None
    if not call.args and not call.keywords:
        return None

    name_expr = call.args[0] if call.args else _keyword_value(call, "key")
    if name_expr is None:
        return None
    default = _default_expression(call)
    return _site(module, call, name_expr, access, default, required=default is None)


def _subscript_site(module: ModuleContext, node: ast.Subscript) -> ConfigSite | None:
    """`os.environ["X"]` and `dotenv_values(...)["X"]`.

    A subscript is only a *read* in Load context; `os.environ["X"] = y` is a
    write and is deliberately not reported as configuration the migration
    must supply.
    """
    if not isinstance(node.ctx, ast.Load):
        return None

    base = _dotted_name(module, node.value)
    if base in _ENVIRON_NAMES:
        return _site(module, node, node.slice, ENVIRON_SUBSCRIPT, default=None, required=True)

    if (
        isinstance(node.value, ast.Call)
        and (resolve_callee_name(module, node.value) or "") in _DOTENV_VALUES
    ):
        return _site(module, node, node.slice, DOTENV, default=None, required=True)
    return None


def _settings_sites(module: ModuleContext, node: ast.ClassDef) -> list[ConfigSite]:
    """Fields of a `BaseSettings` subclass.

    A class qualifies on the last segment of a base name, so
    `BaseSettings`, `pydantic_settings.BaseSettings` and the legacy
    `pydantic.BaseSettings` all match. Only *direct* subclasses are
    recognized: a project's own intermediate base
    (`class Base(BaseSettings)` / `class Real(Base)`) means `Real`'s fields
    are missed -- see the module docstring's gap list.
    """
    if not any(_last_segment(base) in _SETTINGS_BASES for base in node.bases):
        return []

    prefix = _env_prefix(node)
    sites: list[ConfigSite] = []
    for statement in node.body:
        if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
            continue
        attribute = statement.target.id
        if attribute in _SETTINGS_NON_FIELDS or attribute.startswith("_"):
            continue
        default, required = _field_default(statement.value)
        name = _field_alias(statement.value) or f"{prefix}{attribute}".upper()
        sites.append(
            ConfigSite(
                name=name,
                access=PYDANTIC_SETTINGS,
                required=required,
                default=default,
                consumers=_attribute_consumers(module, attribute),
                site=module.site_for(statement),
            )
        )
    return sites


def _env_prefix(node: ast.ClassDef) -> str:
    """`env_prefix` from either `model_config = SettingsConfigDict(...)` (v2)
    or a nested `class Config:` (v1). A non-literal prefix is ignored rather
    than guessed -- the unprefixed name is still closer to the truth than a
    fabricated one.
    """
    for statement in node.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
            if any(
                isinstance(target, ast.Name) and target.id == "model_config"
                for target in statement.targets
            ):
                literal = _keyword_literal(statement.value, "env_prefix")
                if literal is not None:
                    return literal
            continue
        if isinstance(statement, ast.ClassDef) and statement.name == "Config":
            for inner in statement.body:
                if (
                    isinstance(inner, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "env_prefix"
                        for target in inner.targets
                    )
                    and isinstance(inner.value, ast.Constant)
                    and isinstance(inner.value.value, str)
                ):
                    return inner.value.value
    return ""


def _field_default(value: ast.expr | None) -> tuple[str | None, bool]:
    """(default source text, required) for a settings field.

    `x: str` is required. `x: str = Field(...)` is required too -- Ellipsis
    is pydantic's explicit "no default" -- while `x: str = Field("a")` and
    `x: str = "a"` are not.
    """
    if value is None:
        return None, True
    if isinstance(value, ast.Call) and _last_segment(value.func) == "Field":
        explicit = _keyword_value(value, "default")
        if explicit is None and value.args:
            explicit = value.args[0]
        if explicit is None or _is_ellipsis(explicit):
            return None, True
        return ast.unparse(explicit), False
    return ast.unparse(value), False


def _field_alias(value: ast.expr | None) -> str | None:
    """An explicit `Field(alias=...)`/`validation_alias=...`, which overrides
    the field name pydantic-settings looks up.
    """
    if not isinstance(value, ast.Call) or _last_segment(value.func) != "Field":
        return None
    for keyword in ("validation_alias", "alias", "env"):
        literal = _keyword_literal(value, keyword)
        if literal is not None:
            return literal
    return None


# --------------------------------------------------------------------------
# Consumers: where does this value visibly go?
# --------------------------------------------------------------------------


def _consumers(module: ModuleContext, node: ast.expr) -> list[str]:
    """Every callable the value produced at `node` visibly reaches.

    Two paths, both intraprocedural:

    1. `node` sits inside a call's argument list, so that call consumes it
       directly (`ChatOpenAI(api_key=os.getenv("K"))`). Every enclosing call
       counts, so a wrapped value (`ChatOpenAI(api_key=str(os.getenv("K")))`)
       attributes both.
    2. `node` is assigned to a name, and that name appears in a call's
       arguments later in the same scope -- the shape almost every real repo
       uses. Plain `b = a` rebinding is followed a few hops.

    Sorted and de-duplicated: the caller wants the set of callables, and an
    order that depends on AST walk order would make the IR unstable.
    """
    found: set[str] = set()
    found.update(_enclosing_call_consumers(module, node))

    names = _assigned_names(module, node)
    if names:
        scope = _consumer_scope(module, node)
        found.update(_names_reaching_calls(module, scope, names))
    return sorted(found)


def _enclosing_call_consumers(module: ModuleContext, node: ast.AST) -> list[str]:
    """Callables whose argument list `node` sits inside.

    Walking up rather than down: a call reached through the `func` position
    is not a consumer of the value (that is `os.getenv` itself being
    called), so the child that we came from is checked each step.
    """
    consumers: list[str] = []
    current: ast.AST = node
    parent = getattr(current, "parent", None)
    while parent is not None:
        if isinstance(parent, ast.Call):
            if parent.func is current:
                break
            callee = resolve_callee_name(module, parent)
            if callee is not None:
                consumers.append(callee)
        current = parent
        parent = getattr(current, "parent", None)
    return consumers


def _assigned_names(module: ModuleContext, node: ast.AST) -> set[str]:
    """The names `node`'s value is bound to, following `b = a` rebinding.

    Only simple `x = <read>` / `x: T = <read>` bindings; a read destructured
    into a tuple or stored into a dict is not followed, and shows up as a
    site with no consumers rather than a wrong one.
    """
    statement = _enclosing_assignment(node)
    if statement is None:
        return set()
    names = {
        target.id
        for target in _assign_targets(statement)
        if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store)
    }
    if not names:
        return set()

    scope = _consumer_scope(module, node)
    for _ in range(_REBIND_DEPTH):
        grown = set(names)
        for candidate in ast.walk(scope):
            if not isinstance(candidate, (ast.Assign, ast.AnnAssign)):
                continue
            if not isinstance(candidate.value, ast.Name) or candidate.value.id not in names:
                continue
            grown.update(
                target.id for target in _assign_targets(candidate) if isinstance(target, ast.Name)
            )
        if grown == names:
            break
        names = grown
    return names


def _names_reaching_calls(module: ModuleContext, scope: ast.AST, names: set[str]) -> list[str]:
    """Callables that take any of `names` as an argument, anywhere in scope."""
    consumers: list[str] = []
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        callee = resolve_callee_name(module, node)
        if callee is None:
            continue
        arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
        if any(
            isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load) and inner.id in names
            for argument in arguments
            for inner in ast.walk(argument)
        ):
            consumers.append(callee)
    return consumers


def _attribute_consumers(module: ModuleContext, attribute: str) -> list[str]:
    """Callables that receive `settings.<attribute>` anywhere in the module.

    Matched on the attribute name alone, so an unrelated object with the
    same attribute is over-reported. That is the intended direction: an
    over-reported consumer is one line in a review, an omitted one is a
    credential the migration never rewires.
    """
    consumers: set[str] = set()
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Attribute) and node.attr == attribute:
            consumers.update(_enclosing_call_consumers(module, node))
    return sorted(consumers)


def _consumer_scope(module: ModuleContext, node: ast.AST) -> ast.AST:
    """Where to look for uses: the enclosing function, or the whole module
    when the read is at module level (a module-level constant is readable
    from every function in the file, so the whole module is its scope).
    """
    current: ast.AST | None = getattr(node, "parent", None)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = getattr(current, "parent", None)
    return module.tree


# --------------------------------------------------------------------------
# Small AST helpers
# --------------------------------------------------------------------------


def _site(
    module: ModuleContext,
    node: ast.expr,
    name_expr: ast.expr,
    access: str,
    default: str | None,
    required: bool,
) -> ConfigSite:
    name, unresolved = _variable_name(name_expr)
    return ConfigSite(
        name=name,
        access=access,
        required=required,
        default=default,
        consumers=_consumers(module, node),
        site=module.site_for(node),
        unresolved_name=unresolved,
    )


def _variable_name(expr: ast.expr) -> tuple[str, bool]:
    """The env var name, or the source expression marked as unresolved.

    A computed name (`os.getenv(f"{prefix}_KEY")`, `os.environ[key]`) is a
    real configuration read and must reach the ledger; what it cannot do is
    masquerade as a known variable name.
    """
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value, False
    return f"<unresolved: {ast.unparse(expr)}>", True


def _default_expression(call: ast.Call) -> str | None:
    """Source text of the fallback passed to `getenv`/`environ.get`."""
    if len(call.args) >= 2:
        return ast.unparse(call.args[1])
    fallback = _keyword_value(call, "default")
    return ast.unparse(fallback) if fallback is not None else None


def _keyword_value(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _keyword_literal(call: ast.Call, name: str) -> str | None:
    value = _keyword_value(call, name)
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _dotted_name(module: ModuleContext, expr: ast.expr) -> str | None:
    """Alias-resolved dotted name of an arbitrary expression.

    Routed through `resolve_callee_name` on a synthetic call so name
    resolution has exactly one implementation: a second, subtly different
    copy of the alias walk is how `import os as _os` ends up handled in one
    access shape and not the others.
    """
    return resolve_callee_name(module, ast.Call(func=expr, args=[], keywords=[]))


def _last_segment(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return None


def _is_ellipsis(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Constant) and expr.value is Ellipsis


def _enclosing_assignment(node: ast.AST) -> ast.Assign | ast.AnnAssign | None:
    """The assignment whose value *is* this read, or None.

    The walk stops at an intervening `ast.Call`, because past one the
    assigned name holds that call's result, not the environment value:
    `agent = AssistantAgent(llm_config={"api_key": os.getenv("K")})` binds an
    agent to `agent`, and following `agent` from there would attribute every
    later use of the agent to the key. That call is already reported as a
    direct consumer, which is the accurate fact.
    """
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, (ast.Assign, ast.AnnAssign)):
            return current
        if (
            isinstance(current, (ast.Call, ast.FunctionDef, ast.AsyncFunctionDef, ast.Module))
            and current is not node
        ):
            return None
        current = getattr(current, "parent", None)
    return None


def _assign_targets(statement: ast.Assign | ast.AnnAssign) -> list[ast.expr]:
    return statement.targets if isinstance(statement, ast.Assign) else [statement.target]
