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
    """
    repo = _repo(
        tmp_path,
        "from haystack.components.generators import OpenAIGenerator\n"
        'gen = OpenAIGenerator(model="gpt-4o")\n',
    )

    sites = find_llm_call_sites(build_repo_graph(repo))

    assert [(s.client, s.model, s.known) for s in sites] == [("OpenAIGenerator", "gpt-4o", False)]


def test_finds_a_model_named_only_inside_a_config_dict(tmp_path: Path) -> None:
    """AutoGen puts the entire model identity inside `llm_config`."""
    repo = _repo(tmp_path, 'a = AssistantAgent(name="a", llm_config={"model": "gpt-4o"})\n')

    sites = find_llm_call_sites(build_repo_graph(repo))

    assert [(s.client, s.model, s.known) for s in sites] == [("AssistantAgent", "gpt-4o", False)]


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
