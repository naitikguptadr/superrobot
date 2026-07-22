"""LLM provider registry — single source of truth for scan, migrate, and gateway shim."""

from __future__ import annotations

# Client constructor → dr_llm shim function (generated into agent bundle)
LLM_CLIENT_SHIMS: dict[str, str] = {
    # OpenAI / Azure (langchain + raw SDK)
    "ChatOpenAI": "dr_chat_openai",
    "AzureChatOpenAI": "dr_azure_chat_openai",
    "OpenAI": "dr_openai",
    "AsyncOpenAI": "dr_async_openai",
    # Anthropic
    "ChatAnthropic": "dr_chat_anthropic",
    "Anthropic": "dr_anthropic",
    "AsyncAnthropic": "dr_async_anthropic",
    # Google / Vertex
    "ChatGoogleGenerativeAI": "dr_chat_google",
    "ChatVertexAI": "dr_chat_vertex",
    # AWS / Groq / local
    "ChatBedrock": "dr_chat_bedrock",
    "ChatGroq": "dr_chat_groq",
    "ChatOllama": "dr_chat_ollama",
    "ChatMistralAI": "dr_chat_mistral",
    "ChatCohere": "dr_chat_cohere",
    # LiteLLM / instructor wrappers
    "completion": "dr_litellm_completion",
}

# All constructor names the scanner watches for in call sites
LLM_CONSTRUCTORS: frozenset[str] = frozenset(LLM_CLIENT_SHIMS)

# Import prefixes → logical provider (for scan metadata + .env.template hints)
PROVIDER_IMPORT_PREFIXES: dict[str, str] = {
    "langchain_openai": "openai",
    "openai": "openai",
    "langchain_anthropic": "anthropic",
    "anthropic": "anthropic",
    "langchain_google_genai": "google",
    "langchain_google_vertexai": "google",
    "langchain_aws": "aws",
    "langchain_groq": "groq",
    "langchain_ollama": "ollama",
    "langchain_mistralai": "mistral",
    "langchain_cohere": "cohere",
    "litellm": "litellm",
    "instructor": "openai",
}

# Env vars commonly required per provider (documented in generated .env.template)
PROVIDER_ENV_VARS: dict[str, list[str]] = {
    "openai": ["OPENAI_API_KEY", "OPENAI_API_BASE"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "google": ["GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"],
    "aws": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"],
    "groq": ["GROQ_API_KEY"],
    "ollama": ["OLLAMA_BASE_URL"],
    "mistral": ["MISTRAL_API_KEY"],
    "cohere": ["CO_API_KEY"],
    "azure": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"],
    "litellm": ["LITELLM_API_BASE"],
}

# DR platform vars — always injected on deployment
DR_PLATFORM_VARS = ["DATAROBOT_ENDPOINT", "DATAROBOT_API_TOKEN", "PROMPT_TEMPLATE_ID"]


def detect_providers_from_imports(module: str) -> set[str]:
    """Map a Python import module string to provider ids."""
    found: set[str] = set()
    for prefix, provider in PROVIDER_IMPORT_PREFIXES.items():
        if module.startswith(prefix):
            found.add(provider)
    return found


def provider_env_hints(providers: set[str]) -> list[str]:
    """Env vars to document for detected providers."""
    hints: list[str] = []
    for provider in sorted(providers):
        hints.extend(PROVIDER_ENV_VARS.get(provider, []))
    return list(dict.fromkeys(hints))
