"""LLM call-site discovery -- must find sites it cannot handle, not skip them."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.probes.llm_calls import find_llm_call_sites


def _repo(tmp_path: Path, source: str) -> Path:
    (tmp_path / "main.py").write_text(source)
    return tmp_path


def test_finds_a_known_client_and_resolves_its_model(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'llm = ChatOpenAI(model="gpt-4o")\n')

    sites = find_llm_call_sites(build_repo_graph(repo))

    assert len(sites) == 1
    assert sites[0].client == "ChatOpenAI"
    assert sites[0].model == "gpt-4o"
    assert sites[0].known is True


def test_finds_an_aliased_client(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "CLS = ChatOpenAI\nllm = CLS(model='gpt-4o')\n")

    sites = find_llm_call_sites(build_repo_graph(repo))

    assert [s.client for s in sites] == ["ChatOpenAI"]


def test_reports_an_unknown_client_instead_of_ignoring_it(tmp_path: Path) -> None:
    """The governing invariant: a provider we have no shim for must still be
    surfaced, so the coverage ledger can block on it.
    """
    repo = _repo(tmp_path, 'llm = ChatFireworks(model="llama-v3")\n')

    sites = find_llm_call_sites(build_repo_graph(repo))

    assert len(sites) == 1
    assert sites[0].client == "ChatFireworks"
    assert sites[0].known is False


def test_does_not_match_inside_a_string_literal(tmp_path: Path) -> None:
    """The regex implementation rewrote the name inside strings."""
    repo = _repo(tmp_path, 'log("we call ChatOpenAI(...) here")\n')

    assert find_llm_call_sites(build_repo_graph(repo)) == []


def test_every_site_carries_provenance(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'llm = ChatOpenAI(model="gpt-4o")\n')

    site = find_llm_call_sites(build_repo_graph(repo))[0]

    assert site.site.file.endswith("main.py")
    assert site.site.line == 1


# --- Beyond the plan's cases: patterns found in the repo's own fixtures ---
# Each of these is a real shape from tests/fixtures that the first pass of
# the heuristic missed. A missed LLM call ships a broken agent, so they are
# pinned here.


def test_finds_a_module_qualified_call(tmp_path: Path) -> None:
    """The regex's lookbehind explicitly refused `lo.ChatOpenAI(...)`."""
    repo = _repo(
        tmp_path,
        'import langchain_openai as lo\nllm = lo.ChatOpenAI(model="gpt-4o")\n',
    )

    sites = find_llm_call_sites(build_repo_graph(repo))

    assert [(s.client, s.model, s.known) for s in sites] == [("ChatOpenAI", "gpt-4o", True)]


def test_finds_a_model_bearing_client_that_is_not_named_chat_anything(tmp_path: Path) -> None:
    """Haystack's OpenAIGenerator is nobody's `Chat*` class, but it is an
    LLM client and the migration must account for it.

    `known` used to be False here because we had no shim for it. It is now
    True because `known` means "we named the provider", and we do: the
    haystack module plus the constructor identify OpenAI.
    """
    repo = _repo(
        tmp_path,
        "from haystack.components.generators import OpenAIGenerator\n"
        'gen = OpenAIGenerator(model="gpt-4o")\n',
    )

    sites = find_llm_call_sites(build_repo_graph(repo))

    assert [(s.client, s.model, s.known, s.provider) for s in sites] == [
        ("OpenAIGenerator", "gpt-4o", True, "openai")
    ]


def test_finds_a_model_named_only_inside_a_config_dict(tmp_path: Path) -> None:
    """AutoGen puts the entire model identity inside `llm_config`."""
    repo = _repo(tmp_path, 'a = AssistantAgent(name="a", llm_config={"model": "gpt-4o"})\n')

    sites = find_llm_call_sites(build_repo_graph(repo))

    assert [(s.client, s.model, s.known) for s in sites] == [("AssistantAgent", "gpt-4o", False)]


def test_resolves_the_provider_for_a_known_client(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'llm = ChatOpenAI(model="gpt-4o")\n')

    site = find_llm_call_sites(build_repo_graph(repo))[0]

    assert site.provider == "openai"
    assert site.known is True
    assert site.implicit_model is False


def test_an_unrecognized_client_is_reported_with_no_provider(tmp_path: Path) -> None:
    """`known=False` now means "we could not name the provider" -- the site is
    still reported, with `provider=None` standing for the gap.
    """
    repo = _repo(tmp_path, 'llm = ChatFireworks(model="llama-v3")\n')

    site = find_llm_call_sites(build_repo_graph(repo))[0]

    assert site.provider is None
    assert site.known is False
    assert site.model == "llama-v3"


def test_resolves_the_provider_through_the_import_module(tmp_path: Path) -> None:
    """Semantic Kernel's `OpenAIChatCompletion` is a real OpenAI client."""
    repo = _repo(
        tmp_path,
        "from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion\n"
        'svc = OpenAIChatCompletion(ai_model_id="gpt-4o")\n',
    )

    site = find_llm_call_sites(build_repo_graph(repo))[0]

    assert (site.client, site.provider, site.known, site.model) == (
        "OpenAIChatCompletion",
        "openai",
        True,
        "gpt-4o",
    )


def test_finds_crewai_agents_that_name_no_model_at_all(tmp_path: Path) -> None:
    """CrewAI's `Agent`/`Crew` call a model configured by the framework. That
    is a migratable fact -- the target recipe must be told a model -- so it
    cannot stay invisible.
    """
    repo = _repo(
        tmp_path,
        "from crewai import Agent, Crew, Task\n"
        'r = Agent(role="Researcher", goal="Research", backstory="Expert")\n'
        't = Task(description="go", agent=r)\n'
        "c = Crew(agents=[r], tasks=[t])\n",
    )

    sites = find_llm_call_sites(build_repo_graph(repo))

    assert [(s.client, s.provider, s.model, s.implicit_model) for s in sites] == [
        ("Agent", "crewai", None, True),
        ("Crew", "crewai", None, True),
    ]


def test_finds_a_llamaindex_query_engine_that_names_no_model(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "from llama_index.core import VectorStoreIndex\n"
        "index = VectorStoreIndex.from_documents([])\n"
        "engine = index.as_query_engine()\n",
    )

    sites = find_llm_call_sites(build_repo_graph(repo))

    assert [(s.client, s.provider, s.model, s.implicit_model) for s in sites] == [
        ("as_query_engine", "llama_index", None, True)
    ]


def test_an_explicit_model_is_not_reported_as_implicit(tmp_path: Path) -> None:
    """`implicit_model` means *no model is named here*. Naming one turns it
    off, or the ledger would demand a model that is already present.
    """
    repo = _repo(
        tmp_path,
        "from crewai import Agent\nr = Agent(role='R', goal='G', llm='gpt-4o')\n",
    )

    site = find_llm_call_sites(build_repo_graph(repo))[0]

    assert (site.model, site.implicit_model) == ("gpt-4o", False)


def test_does_not_flag_ordinary_framework_plumbing(tmp_path: Path) -> None:
    """Generous is not indiscriminate: an orchestration call with no model
    in sight is not an LLM call, and flagging it would bury the real ones.
    """
    repo = _repo(
        tmp_path,
        "from langgraph.graph import StateGraph\n"
        "from langchain_core.prompts import ChatPromptTemplate\n"
        "g = StateGraph(dict)\n"
        'g.add_node("planner", planner)\n'
        'prompt = ChatPromptTemplate.from_messages([("system", "hi")])\n',
    )

    assert find_llm_call_sites(build_repo_graph(repo)) == []
