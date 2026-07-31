"""Which provider is a given call site talking to?

Under the IR architecture we no longer rewrite source to point at a shim, so
"do we have a shim for this client?" stopped being the interesting question.
The interesting question is the one the target recipe actually needs answered:
**which provider, and which model?** This module answers the first half.

How a provider is resolved
--------------------------
Two keys, in order of authority:

1. **The module the call resolves into.** `resolve_callee_name` in
   `graph.dataflow` follows imports and rebinding, so a call site hands us a
   dotted name like `langchain_openai.ChatOpenAI` or
   `haystack.components.generators.OpenAIGenerator`. The longest matching
   module prefix wins, so `autogen_ext.models.openai` beats `autogen_ext`.
2. **The bare constructor name**, but only for names that identify a provider
   on their own (`ChatOpenAI`, `OpenAIGenerator`, `HfApiModel`). This is the
   fallback for calls whose import we could not follow.

The module has to come first because bare names are ambiguous: `OpenAI` is
the openai SDK's client *and* LlamaIndex's, `Anthropic` is both SDKs, and
`Agent` is anybody's.

Two kinds of module prefix
--------------------------
* A **provider module** (`openai`, `langchain_anthropic`, `litellm`) is
  authoritative by itself: anything reached through it speaks to that
  provider.
* A **framework module** (`crewai`, `haystack`, `autogen`) is not. Frameworks
  mediate many providers and contain far more plumbing than clients, so a
  framework prefix only narrows the search: the constructor must additionally
  appear in that framework's table, or in the self-identifying name table.
  `crewai.Task` and `haystack.Pipeline` therefore resolve to nothing.

What it deliberately refuses to do
----------------------------------
There is no "starts with `Chat` -> it's a provider" rule, and no
"contains the token `OpenAI`" rule. Those would manufacture a provider name
for `ChatFireworks` -- a real LangChain integration we have no entry for --
and a fabricated provider is worse than an admitted gap, because the gap is
the thing the coverage ledger is supposed to block on. Every entry below is
an explicit claim about a real framework. Anything else resolves to None and
is reported as unresolved.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Provider modules: authoritative on their own
# --------------------------------------------------------------------------

#: Import module prefix -> logical provider. A call reaching anywhere under
#: one of these prefixes is talking to that provider; these packages exist for
#: no other purpose.
PROVIDER_MODULES: dict[str, str] = {
    # Raw provider SDKs.
    "openai": "openai",
    "anthropic": "anthropic",
    "cohere": "cohere",
    "mistralai": "mistral",
    "groq": "groq",
    "ollama": "ollama",
    "litellm": "litellm",
    "vertexai": "google",
    "google.generativeai": "google",
    "google.genai": "google",
    # `google` alone is a namespace package shared with protobuf, auth, and
    # the cloud SDKs, so only the model-serving subpackages are claimed.
    "google.cloud.aiplatform": "google",
    "instructor": "openai",
    # LangChain partner packages: one package per provider, by construction.
    "langchain_openai": "openai",
    "langchain_anthropic": "anthropic",
    "langchain_google_genai": "google",
    "langchain_google_vertexai": "google",
    "langchain_aws": "bedrock",
    "langchain_groq": "groq",
    "langchain_ollama": "ollama",
    "langchain_mistralai": "mistral",
    "langchain_cohere": "cohere",
    "langchain_litellm": "litellm",
    "langchain_huggingface": "huggingface",
    # Framework subpackages that are provider-specific by name.
    "autogen_ext.models.openai": "openai",
    "autogen_ext.models.anthropic": "anthropic",
    "llama_index.llms.openai": "openai",
    "llama_index.llms.azure_openai": "azure",
    "llama_index.llms.anthropic": "anthropic",
    "llama_index.llms.bedrock": "bedrock",
    "llama_index.llms.ollama": "ollama",
    "llama_index.llms.groq": "groq",
    "llama_index.llms.mistralai": "mistral",
    "llama_index.llms.cohere": "cohere",
    "llama_index.llms.litellm": "litellm",
}

# --------------------------------------------------------------------------
# Framework modules: narrow the search, but never conclude on their own
# --------------------------------------------------------------------------

#: Import module prefix -> the framework it belongs to. Several prefixes map
#: to one framework (`langchain`, `langchain_core`, `langgraph`).
FRAMEWORK_MODULES: dict[str, str] = {
    "langchain": "langchain",
    "langchain_core": "langchain",
    "langchain_community": "langchain",
    "langgraph": "langchain",
    "autogen": "autogen",
    "autogen_agentchat": "autogen",
    "autogen_ext": "autogen",
    "haystack": "haystack",
    "haystack_integrations": "haystack",
    "semantic_kernel": "semantic_kernel",
    "smolagents": "smolagents",
    "crewai": "crewai",
    "crewai_tools": "crewai",
    "llama_index": "llama_index",
}

#: (framework, constructor) -> provider, for constructors whose own name says
#: nothing about a provider. These are the framework's agent/orchestration
#: classes: `AssistantAgent`, `Agent`, `CodeAgent`. Plenty of codebases define
#: a class called `Agent`, so the module is what makes the entry a fact.
FRAMEWORK_CLIENT_PROVIDERS: dict[tuple[str, str], str] = {
    # AutoGen. The agent classes take an `llm_config`, so the framework -- not
    # the class -- is what mediates the provider choice.
    ("autogen", "AssistantAgent"): "autogen",
    ("autogen", "UserProxyAgent"): "autogen",
    ("autogen", "ConversableAgent"): "autogen",
    ("autogen", "GroupChatManager"): "autogen",
    # smolagents. The agents take a model *object*; the model classes below
    # name their own provider.
    ("smolagents", "CodeAgent"): "smolagents",
    ("smolagents", "ToolCallingAgent"): "smolagents",
    ("smolagents", "MultiStepAgent"): "smolagents",
    ("smolagents", "TransformersModel"): "huggingface",
    ("smolagents", "MLXModel"): "huggingface",
    ("smolagents", "VLLMModel"): "huggingface",
    # CrewAI. `Agent` and `Crew` run on a model configured by the framework;
    # `crewai.LLM` is a thin wrapper over LiteLLM.
    ("crewai", "Agent"): "crewai",
    ("crewai", "Crew"): "crewai",
    ("crewai", "LLM"): "litellm",
    # LlamaIndex. `Settings` carries the global model configuration.
    ("llama_index", "Settings"): "llama_index",
}

# --------------------------------------------------------------------------
# Self-identifying constructor names
# --------------------------------------------------------------------------

#: Constructor name -> provider, for names that identify a provider without
#: any module context. Every entry is a specific class from a specific
#: framework, never a name shape.
CLIENT_PROVIDERS: dict[str, str] = {
    # LangChain chat models.
    "ChatOpenAI": "openai",
    "AzureChatOpenAI": "azure",
    "ChatAnthropic": "anthropic",
    "ChatGoogleGenerativeAI": "google",
    "ChatVertexAI": "google",
    "ChatBedrock": "bedrock",
    "ChatBedrockConverse": "bedrock",
    "BedrockChat": "bedrock",
    "BedrockLLM": "bedrock",
    "ChatGroq": "groq",
    "ChatOllama": "ollama",
    "OllamaLLM": "ollama",
    "ChatMistralAI": "mistral",
    "ChatCohere": "cohere",
    "ChatLiteLLM": "litellm",
    "ChatHuggingFace": "huggingface",
    "HuggingFaceEndpoint": "huggingface",
    "HuggingFacePipeline": "huggingface",
    "VertexAI": "google",
    # Raw SDK clients.
    "OpenAI": "openai",
    "AsyncOpenAI": "openai",
    "AzureOpenAI": "azure",
    "AsyncAzureOpenAI": "azure",
    "Anthropic": "anthropic",
    "AsyncAnthropic": "anthropic",
    "AnthropicBedrock": "bedrock",
    "AnthropicVertex": "google",
    "Groq": "groq",
    "AsyncGroq": "groq",
    "Mistral": "mistral",
    "MistralClient": "mistral",
    "GenerativeModel": "google",
    # AutoGen's model clients (autogen_ext), which do name their provider.
    "OpenAIChatCompletionClient": "openai",
    "AzureOpenAIChatCompletionClient": "azure",
    "AnthropicChatCompletionClient": "anthropic",
    # Haystack generators.
    "OpenAIGenerator": "openai",
    "OpenAIChatGenerator": "openai",
    "AzureOpenAIGenerator": "azure",
    "AzureOpenAIChatGenerator": "azure",
    "AnthropicGenerator": "anthropic",
    "AnthropicChatGenerator": "anthropic",
    "CohereGenerator": "cohere",
    "CohereChatGenerator": "cohere",
    "OllamaGenerator": "ollama",
    "OllamaChatGenerator": "ollama",
    "HuggingFaceAPIGenerator": "huggingface",
    "HuggingFaceAPIChatGenerator": "huggingface",
    "HuggingFaceLocalGenerator": "huggingface",
    # Semantic Kernel connectors.
    "OpenAIChatCompletion": "openai",
    "OpenAITextCompletion": "openai",
    "AzureChatCompletion": "azure",
    "AzureTextCompletion": "azure",
    "AnthropicChatCompletion": "anthropic",
    "GoogleAIChatCompletion": "google",
    "VertexAIChatCompletion": "google",
    "BedrockChatCompletion": "bedrock",
    "MistralAIChatCompletion": "mistral",
    "OllamaChatCompletion": "ollama",
    # smolagents model classes.
    "HfApiModel": "huggingface",
    "InferenceClientModel": "huggingface",
    "LiteLLMModel": "litellm",
    "OpenAIServerModel": "openai",
    "AzureOpenAIServerModel": "azure",
    # LlamaIndex. `OpenAI`/`Anthropic`/... are shared with the raw SDKs above
    # and resolve to the same provider either way, so only the names unique to
    # LlamaIndex need listing.
    "OpenAILike": "openai",
    "HuggingFaceLLM": "huggingface",
    # LlamaIndex builds its LLM call out of an index rather than a client, so
    # the engine constructors are the call site. The names are distinctive
    # enough to stand without a module -- `index` is a local variable, so
    # there is no import to resolve.
    "as_query_engine": "llama_index",
    "as_chat_engine": "llama_index",
}

#: Constructors that will call a model but name none, so the framework's
#: default applies. These produce a call site with `model=None` and
#: `implicit_model=True`: an invisible default is still a decision the target
#: recipe has to be told about explicitly.
IMPLICIT_MODEL_CLIENTS: frozenset[str] = frozenset(
    {
        # CrewAI reads the model from `OPENAI_MODEL_NAME`/its own defaults.
        "Agent",
        "Crew",
        # LlamaIndex reads it from the global `Settings`.
        "as_query_engine",
        "as_chat_engine",
        # smolagents: the agent takes a model object, and the Hub-backed model
        # classes default to whatever the endpoint serves.
        "CodeAgent",
        "ToolCallingAgent",
        "MultiStepAgent",
        "HfApiModel",
        "InferenceClientModel",
    }
)


def resolve_provider(callee_dotted_name: str, client_name: str) -> str | None:
    """The logical provider a call site speaks to, or None if we cannot say.

    `callee_dotted_name` is the fully-qualified name from
    `dataflow.resolve_callee_name` (`langchain_openai.ChatOpenAI`), and
    `client_name` its last segment. The module prefix is preferred because it
    is authoritative; the bare name is a fallback for calls whose import could
    not be followed.

    Returning None is a real answer, not a failure: it means the call site is
    reported as an unrecognized provider rather than assigned a guessed one.
    """
    module = callee_dotted_name.rsplit(".", 1)[0] if "." in callee_dotted_name else ""
    provider, framework = _longest_module_match(module)
    if provider is not None:
        return provider
    if framework is not None:
        return FRAMEWORK_CLIENT_PROVIDERS.get((framework, client_name)) or CLIENT_PROVIDERS.get(
            client_name
        )
    return CLIENT_PROVIDERS.get(client_name)


def _longest_module_match(module: str) -> tuple[str | None, str | None]:
    """The most specific module prefix of `module`, as (provider, framework).

    At most one of the two is set: the longest prefix wins outright, so
    `autogen_ext.models.openai` resolves to the openai provider rather than
    stopping at the `autogen_ext` framework.
    """
    provider: str | None = None
    framework: str | None = None
    if not module:
        return None, None
    parts = module.split(".")
    for index in range(1, len(parts) + 1):
        prefix = ".".join(parts[:index])
        if prefix in PROVIDER_MODULES:
            provider, framework = PROVIDER_MODULES[prefix], None
        elif prefix in FRAMEWORK_MODULES:
            provider, framework = None, FRAMEWORK_MODULES[prefix]
    return provider, framework
