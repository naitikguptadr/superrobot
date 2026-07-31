"""Find every LLM invocation in a repo -- including the ones we have no
shim for -- as facts with provenance.

Detection is entirely AST-based. A name is only ever considered after it
has been resolved through the module's import and assignment bindings
(`graph.dataflow.resolve_callee_name`), so this probe sees *calls*, not
text. The regex it replaces matched the characters `ChatOpenAI(` anywhere
in a file, which meant it rewrote the name inside string literals and
comments while simultaneously missing every call that didn't spell the
constructor out literally at the call site.

Two ways a call is recognized
-----------------------------
1. **Known** (`known=True`): `probes.providers.resolve_provider` named the
   provider, from the module the call resolves into or from a constructor
   we have an explicit entry for. This no longer has anything to do with
   having a shim -- under the IR architecture we recompile onto DataRobot's
   recipe rather than rewriting source, so the question that matters is
   "can we name the provider and the model?", not "do we have a shim?".
2. **Unknown** (`known=False`): no provider could be named, but the call
   matches an open heuristic below. The ledger must still see it -- a
   provider that is silently skipped is a broken migration; a provider that
   is over-reported is one line in a review.

The unknown-provider heuristic catches
--------------------------------------
* names beginning with `Chat` (`ChatFireworks`, `ChatTogether`,
  `ChatPerplexity` -- the long tail of LangChain integrations we will never
  finish enumerating) and names ending in `LLM`/`Llm`/`ChatModel`
* a small set of well-known client constructors that don't start with
  `Chat` (`AzureOpenAI`, `Groq`, `GenerativeModel`, ...)
* any call resolving into a *provider SDK* package -- `openai`,
  `anthropic`, `litellm`, `cohere`, `mistralai`, `groq`, `ollama`,
  `vertexai`, `together`, `fireworks`, `replicate`, plus the
  provider-specific LangChain integration packages from
  `providers.PROVIDER_IMPORT_PREFIXES`, which exist for no purpose other
  than constructing model clients
* raw SDK completion calls by their method shape --
  `client.chat.completions.create(...)`, `client.messages.create(...)`,
  `model.generate_content(...)` -- which resolve through no import at all
* any name containing a provider brand (`OpenAIGenerator` in Haystack,
  `OpenAIChatCompletion` in Semantic Kernel, `HfApiModel` in smolagents):
  every framework invents its own suffix, but they all keep the brand
* as a last resort, *any* call that has a model wired into it -- a
  `model`/`model_name`/`model_id`/`ai_model_id`/`azure_deployment` argument,
  or a config bundle (`llm_config=`, `llm=`, `model_client=`). Whatever it
  is, it is a place the migration must account for. The model is read out
  of a literal config dict too, so AutoGen's
  `llm_config={"model": "gpt-4o"}` resolves.

The implicit-default-model case
-------------------------------
CrewAI's `Agent(role=..., goal=...)` and LlamaIndex's
`index.as_query_engine()` call a model that is named nowhere at the call
site -- the framework supplies its own default from the environment or a
global `Settings`. That is still a migratable fact, and the loudest one:
the target recipe must be told a model explicitly, so an invisible default
is exactly what must not stay invisible. These produce a call site with
`model=None` and `implicit_model=True`, driven by
`providers.IMPLICIT_MODEL_CLIENTS`.

It deliberately does NOT catch
------------------------------
* calls through a value we cannot name at all (`get_client()(...)`,
  `handlers[key](...)`): there is no callee name to classify. Interprocedural
  resolution is a Phase 2 non-goal, so these are invisible to this probe.
* prompt/message types whose names merely start with `Chat`
  (`ChatPromptTemplate`, `ChatMessage`, ...) -- see `_NOT_CLIENTS`. These
  are still reported if they are known shims, and still reported if a model
  is wired into them; the denylist only blocks the name-shape rules.
* an LLM call hidden behind a project's own wrapper function
  (`_invoke_model()`): the wrapper's *body* contains the real call and is
  found there, but the wrapper's own call sites are not attributed. Naming
  such wrappers is Layer 3's (the LLM interpretation layer's) job.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from superrobot.engine.providers import PROVIDER_IMPORT_PREFIXES
from superrobot.pipeline.graph.builder import RepoGraph
from superrobot.pipeline.graph.dataflow import (
    ModuleContext,
    Site,
    analyze_modules,
    resolve_call_parameter,
    resolve_callee_name,
)
from superrobot.pipeline.probes.providers import IMPLICIT_MODEL_CLIENTS, resolve_provider

# Parameter names that carry the model identity, in the order we trust them.
# Different SDKs spell it differently and getting this wrong means reporting
# "model unknown" for a call whose model is written right there.
_MODEL_PARAMETERS = (
    "model",
    "model_name",
    "model_id",
    "ai_model_id",
    "azure_deployment",
    "deployment_name",
)

# Keyword arguments that hand a whole model configuration to a framework
# rather than naming the model directly (AutoGen's `llm_config`, CrewAI's
# `llm`). Their presence is itself evidence of an LLM wiring point.
_MODEL_CONFIG_PARAMETERS = ("llm_config", "llm", "model_client", "model_kwargs", "config")

# Brand tokens. A constructor whose name contains one is a model client
# under some naming convention we haven't seen -- `OpenAIGenerator`
# (Haystack), `OpenAIChatCompletion` (Semantic Kernel), `HfApiModel`
# (smolagents). Enumerating every framework's conventions is exactly the
# losing game the 14-name regex played; matching the provider brand is not.
_PROVIDER_BRANDS = (
    "OpenAI",
    "Azure",
    "Anthropic",
    "Claude",
    "Gemini",
    "GoogleGenerative",
    "Vertex",
    "Bedrock",
    "Cohere",
    "Mistral",
    "Groq",
    "Ollama",
    "Llama",
    "HuggingFace",
    "HfApi",
    "Fireworks",
    "Together",
    "Perplexity",
    "DeepSeek",
    "LiteLLM",
)

# Top-level packages that exist to talk to a model provider. A call landing
# anywhere in one of these is worth surfacing. Orchestration frameworks are
# deliberately absent -- see the module docstring.
_PROVIDER_PACKAGES = frozenset(
    {
        "openai",
        "anthropic",
        "litellm",
        "cohere",
        "mistralai",
        "groq",
        "ollama",
        "vertexai",
        "together",
        "fireworks",
        "replicate",
        "instructor",
        "google",
        *(prefix.split(".")[0] for prefix in PROVIDER_IMPORT_PREFIXES),
    }
)

# Raw-SDK completion calls, recognized by the shape of the attribute chain
# rather than by any import -- `client` is a variable, so there is nothing to
# resolve, but `client.chat.completions.create(...)` is unmistakable.
_COMPLETION_METHOD_CHAINS = (
    "chat.completions.create",
    "chat.completions.parse",
    "chat.completions.stream",
    "completions.create",
    "responses.create",
    "responses.parse",
    "messages.create",
    "messages.stream",
    "chat.complete",
    "generate_content",
    "create_chat_completion",
)

# Client constructors that don't follow the Chat* convention.
_KNOWN_CLIENT_NAMES = frozenset(
    {
        "AzureOpenAI",
        "AsyncAzureOpenAI",
        "Groq",
        "AsyncGroq",
        "Together",
        "AsyncTogether",
        "Mistral",
        "MistralClient",
        "Fireworks",
        "Replicate",
        "GenerativeModel",
        "LlamaCpp",
        "HuggingFaceEndpoint",
        "HuggingFacePipeline",
        "Bedrock",
        "BedrockChat",
        "BedrockLLM",
        "LiteLLM",
        "VertexAI",
        "OpenAILike",
        "completion",
        "acompletion",
    }
)

# Names that start with `Chat` but are message/prompt types, not clients.
# Only consulted on the heuristic path, never for a known shim.
_NOT_CLIENTS = frozenset(
    {
        "ChatPromptTemplate",
        "ChatMessagePromptTemplate",
        "ChatMessage",
        "ChatMessageChunk",
        "ChatMessageHistory",
        "ChatGeneration",
        "ChatGenerationChunk",
        "ChatResult",
        "ChatSession",
        "ChatCompletion",
        "ChatCompletionChunk",
        "ChatCompletionMessage",
    }
)


@dataclass
class LlmCallSite:
    """One place the repo talks to a model.

    `model` is the statically-resolved model identifier, or None when it
    could not be resolved -- in which case the source expression is still
    present in `params` under the parameter it was passed as, so the fact is
    reported rather than dropped.

    `params` holds every argument at the call site: keyword arguments under
    their own name, positional arguments under `arg0`, `arg1`, ... and a
    `**` unpacking under `**`. Each value is the statically-resolved value
    where one exists, otherwise the source expression.

    `provider` is the logical provider this site talks to (`"openai"`,
    `"bedrock"`, `"crewai"`), resolved by `probes.providers.resolve_provider`
    from the module the call resolves into, falling back to the constructor
    name. None means we could not name it.

    `known` means **we recognized this client and resolved its provider** --
    that is, `provider is not None`. It does *not* mean we have a shim for
    it: shims belonged to the old rewrite-the-source approach, and under the
    IR architecture we recompile onto DataRobot's recipe instead. A site with
    `known=False` is still reported, with `provider=None` marking the gap for
    the coverage ledger to block on.

    `implicit_model` is True when this construct will call a model but names
    none, so the framework's default applies (CrewAI's `Agent`, LlamaIndex's
    `as_query_engine`). `model` is None in that case by definition, and the
    migration must supply a model explicitly.
    """

    client: str
    model: str | None
    known: bool
    site: Site
    params: dict[str, str] = field(default_factory=dict)
    provider: str | None = None
    implicit_model: bool = False


def find_llm_call_sites(repo_graph: RepoGraph) -> list[LlmCallSite]:
    """Every LLM call site in the repo, in file/line order.

    Includes providers we could not name (`known=False`) precisely so the
    coverage ledger can block on them instead of a migration quietly
    shipping without them.
    """
    sites: list[LlmCallSite] = []
    for module in analyze_modules(repo_graph):
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Call):
                continue
            callee = resolve_callee_name(module, node)
            if callee is None:
                continue
            client = callee.rsplit(".", 1)[-1]
            provider = resolve_provider(callee, client)
            if provider is None and not _looks_like_an_llm_call(callee, client, node):
                continue
            model = _resolve_model(module, node)
            sites.append(
                LlmCallSite(
                    client=client,
                    model=model,
                    known=provider is not None,
                    site=module.site_for(node),
                    params=_call_params(module, node),
                    provider=provider,
                    implicit_model=model is None and client in IMPLICIT_MODEL_CLIENTS,
                )
            )
    return sorted(sites, key=lambda s: (s.site.file, s.site.line, s.client))


def _looks_like_an_llm_call(callee: str, client: str, call: ast.Call) -> bool:
    """The open heuristic for call sites whose provider we could not name.

    Tuned to over-report rather than under-report: see the module docstring
    for exactly what it does and does not catch.
    """
    if client in _KNOWN_CLIENT_NAMES:
        return True
    if callee.split(".")[0] in _PROVIDER_PACKAGES:
        return True
    if any(callee.endswith(chain) for chain in _COMPLETION_METHOD_CHAINS):
        return True
    if client in _NOT_CLIENTS:
        # A prompt/message type is not a client -- unless a model is being
        # wired into it, in which case something unusual is happening and we
        # would rather report it.
        return _names_a_model(call)
    if client.startswith("Chat") or client.endswith(("LLM", "Llm", "ChatModel")):
        return True
    if any(brand in client for brand in _PROVIDER_BRANDS):
        return True
    # Last resort: whatever this is, a model is being wired into it. That
    # makes it a place the migration has to account for, even if we cannot
    # name the framework.
    return _names_a_model(call)


def _names_a_model(call: ast.Call) -> bool:
    """True if the call hands a model, or a model configuration, to whatever
    it constructs.
    """
    return any(
        keyword.arg in _MODEL_PARAMETERS or keyword.arg in _MODEL_CONFIG_PARAMETERS
        for keyword in call.keywords
    )


def _resolve_model(module: ModuleContext, call: ast.Call) -> str | None:
    """The model identifier reaching this call, or None if not statically
    knowable. Never guessed: an unresolvable model stays visible as the
    source expression in `params`.
    """
    for parameter in _MODEL_PARAMETERS:
        if not any(keyword.arg == parameter for keyword in call.keywords):
            continue
        values = resolve_call_parameter(module, call, parameter)
        if len(values.resolved) == 1:
            return values.resolved[0]
        return None
    return _model_from_config_string(module, call) or _model_from_config_dict(call)


def _model_from_config_string(module: ModuleContext, call: ast.Call) -> str | None:
    """CrewAI spells the model as `llm="gpt-4o"` -- a config parameter whose
    value is the model name itself. Only a `llm=` that resolves to a string
    counts; `llm=some_client_object` stays unresolved and visible in `params`.
    """
    if not any(keyword.arg == "llm" for keyword in call.keywords):
        return None
    values = resolve_call_parameter(module, call, "llm")
    return values.resolved[0] if len(values.resolved) == 1 else None


def _model_from_config_dict(call: ast.Call) -> str | None:
    """The model named inside a literal config dict (`llm_config={"model":
    "gpt-4o"}`), which is where whole frameworks keep it.

    Only literal dicts with a literal model key are read. A config assembled
    at runtime stays unresolved, and the expression remains visible in
    `params`.
    """
    for keyword in call.keywords:
        if keyword.arg not in _MODEL_CONFIG_PARAMETERS or not isinstance(keyword.value, ast.Dict):
            continue
        for key, value in zip(keyword.value.keys, keyword.value.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and key.value in _MODEL_PARAMETERS
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                return value.value
    return None


def _call_params(module: ModuleContext, call: ast.Call) -> dict[str, str]:
    """Every argument at the call site, resolved where possible."""
    params: dict[str, str] = {}
    for index, argument in enumerate(call.args):
        params[f"arg{index}"] = _argument_text(module, call, argument)
    for keyword in call.keywords:
        name = keyword.arg or "**"
        params[name] = _argument_text(module, call, keyword.value, parameter=keyword.arg)
    return params


def _argument_text(
    module: ModuleContext,
    call: ast.Call,
    expression: ast.expr,
    parameter: str | None = None,
) -> str:
    if parameter is not None:
        values = resolve_call_parameter(module, call, parameter)
        if len(values.resolved) == 1:
            return values.resolved[0]
    return ast.unparse(expression)
