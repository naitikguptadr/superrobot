"""The provider registry -- what framework/provider a call site belongs to.

The registry answers one question: given the dotted name a call resolves to
and the constructor it names, which logical provider is being spoken to? It
must answer it for the frameworks in `tests/fixtures`, and it must decline to
answer -- returning None -- rather than guess.
"""

from __future__ import annotations

import pytest

from superrobot.pipeline.probes.providers import (
    IMPLICIT_MODEL_CLIENTS,
    resolve_provider,
)


@pytest.mark.parametrize(
    ("callee", "client", "provider"),
    [
        # Provider SDKs and LangChain partner packages: the module is
        # authoritative on its own.
        ("langchain_openai.ChatOpenAI", "ChatOpenAI", "openai"),
        ("langchain_anthropic.ChatAnthropic", "ChatAnthropic", "anthropic"),
        ("langchain_google_genai.ChatGoogleGenerativeAI", "ChatGoogleGenerativeAI", "google"),
        ("langchain_aws.ChatBedrock", "ChatBedrock", "bedrock"),
        ("langchain_groq.ChatGroq", "ChatGroq", "groq"),
        ("langchain_ollama.ChatOllama", "ChatOllama", "ollama"),
        ("langchain_mistralai.ChatMistralAI", "ChatMistralAI", "mistral"),
        ("langchain_cohere.ChatCohere", "ChatCohere", "cohere"),
        ("openai.OpenAI", "OpenAI", "openai"),
        ("anthropic.AsyncAnthropic", "AsyncAnthropic", "anthropic"),
        ("litellm.completion", "completion", "litellm"),
        ("google.generativeai.GenerativeModel", "GenerativeModel", "google"),
        # AutoGen: the agent classes are only meaningful with the module.
        ("autogen.AssistantAgent", "AssistantAgent", "autogen"),
        ("autogen.UserProxyAgent", "UserProxyAgent", "autogen"),
        (
            "autogen_ext.models.openai.OpenAIChatCompletionClient",
            "OpenAIChatCompletionClient",
            "openai",
        ),
        # Haystack.
        ("haystack.components.generators.OpenAIGenerator", "OpenAIGenerator", "openai"),
        (
            "haystack.components.generators.chat.OpenAIChatGenerator",
            "OpenAIChatGenerator",
            "openai",
        ),
        (
            "haystack_integrations.components.generators.anthropic.AnthropicGenerator",
            "AnthropicGenerator",
            "anthropic",
        ),
        # Semantic Kernel.
        (
            "semantic_kernel.connectors.ai.open_ai.OpenAIChatCompletion",
            "OpenAIChatCompletion",
            "openai",
        ),
        (
            "semantic_kernel.connectors.ai.open_ai.AzureChatCompletion",
            "AzureChatCompletion",
            "azure",
        ),
        # smolagents.
        ("smolagents.HfApiModel", "HfApiModel", "huggingface"),
        ("smolagents.InferenceClientModel", "InferenceClientModel", "huggingface"),
        ("smolagents.LiteLLMModel", "LiteLLMModel", "litellm"),
        ("smolagents.OpenAIServerModel", "OpenAIServerModel", "openai"),
        ("smolagents.CodeAgent", "CodeAgent", "smolagents"),
        ("smolagents.ToolCallingAgent", "ToolCallingAgent", "smolagents"),
        # CrewAI.
        ("crewai.Agent", "Agent", "crewai"),
        ("crewai.Crew", "Crew", "crewai"),
        ("crewai.LLM", "LLM", "litellm"),
        # LlamaIndex.
        ("llama_index.llms.openai.OpenAI", "OpenAI", "openai"),
        ("llama_index.llms.anthropic.Anthropic", "Anthropic", "anthropic"),
        ("index.as_query_engine", "as_query_engine", "llama_index"),
    ],
)
def test_resolves_the_provider_for_every_framework_we_claim_to_support(
    callee: str, client: str, provider: str
) -> None:
    assert resolve_provider(callee, client) == provider


def test_prefers_the_import_module_over_the_bare_client_name() -> None:
    """`OpenAI` is the openai SDK client *and* a LlamaIndex client. The module
    the call resolves into is the authoritative answer; the bare name is only
    a fallback.
    """
    assert resolve_provider("llama_index.llms.openai.OpenAI", "OpenAI") == "openai"
    assert resolve_provider("openai.OpenAI", "OpenAI") == "openai"


def test_declines_to_name_a_provider_for_an_unrecognized_client() -> None:
    """ChatFireworks is a real LangChain integration we have no entry for.
    Guessing a provider from the `Chat` prefix would fabricate a fact.
    """
    assert resolve_provider("ChatFireworks", "ChatFireworks") is None
    assert (
        resolve_provider("langchain_community.chat_models.ChatFireworks", "ChatFireworks") is None
    )


def test_a_framework_agent_class_needs_its_module_to_be_recognized() -> None:
    """`AssistantAgent` names no provider by itself -- plenty of codebases
    define their own. Only the resolved `autogen.` module makes it a fact.
    """
    assert resolve_provider("AssistantAgent", "AssistantAgent") is None
    assert resolve_provider("Agent", "Agent") is None
    assert resolve_provider("Crew", "Crew") is None


def test_does_not_claim_ordinary_framework_plumbing() -> None:
    """Being inside a supported framework is not enough; the constructor has
    to be one that talks to a model.
    """
    assert resolve_provider("crewai.Task", "Task") is None
    assert resolve_provider("haystack.Pipeline", "Pipeline") is None
    assert resolve_provider("langgraph.graph.StateGraph", "StateGraph") is None
    assert (
        resolve_provider("langchain_core.prompts.ChatPromptTemplate.from_messages", "from_messages")
        is None
    )
    assert resolve_provider("smolagents.DuckDuckGoSearchTool", "DuckDuckGoSearchTool") is None


def test_the_longest_matching_module_prefix_wins() -> None:
    """`autogen_ext.models.openai` is more specific than `autogen_ext`, and it
    names a real provider rather than the framework that mediates one.
    """
    assert (
        resolve_provider("autogen_ext.models.openai.OpenAIChatCompletionClient", "SomethingNew")
        == "openai"
    )


def test_names_the_constructs_that_use_a_model_without_naming_one() -> None:
    """These are the framework-default-model cases. They must be listed
    explicitly, because the migration target has to be told a model.
    """
    for client in ("Agent", "Crew", "as_query_engine", "CodeAgent"):
        assert client in IMPLICIT_MODEL_CLIENTS
    assert "ChatOpenAI" not in IMPLICIT_MODEL_CLIENTS
