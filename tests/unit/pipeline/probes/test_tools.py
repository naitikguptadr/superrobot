"""Tool discovery -- a tool the probe misses is a capability the migrated
agent silently loses, so the bias is toward reporting with a low-confidence
`detection` rather than staying quiet.
"""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.probes.tools import ToolSite, find_tool_sites

FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures"


def _repo(tmp_path: Path, source: str) -> Path:
    (tmp_path / "main.py").write_text(source)
    return tmp_path


def _find(tmp_path: Path, source: str) -> list[ToolSite]:
    return find_tool_sites(build_repo_graph(_repo(tmp_path, source)))


# --- decorators -----------------------------------------------------------


def test_finds_a_decorated_async_tool_with_its_full_signature(tmp_path: Path) -> None:
    sites = _find(
        tmp_path,
        "from langchain_core.tools import tool\n"
        "\n"
        "@tool\n"
        "async def web_search(query: str, limit: int = 3) -> list[str]:\n"
        '    """Search the web for sources."""\n'
        "    return []\n",
    )

    assert len(sites) == 1
    site = sites[0]
    assert site.name == "web_search"
    assert site.callable == "main.web_search"
    assert site.description == "Search the web for sources."
    assert [(a.name, a.type) for a in site.inputs] == [("query", "str"), ("limit", "int")]
    assert [(a.name, a.type) for a in site.outputs] == [("return", "list[str]")]
    assert site.is_async is True
    assert site.decorator == "langchain_core.tools.tool"
    assert site.detection == "decorator:langchain_core.tools.tool"


def test_resolves_an_aliased_decorator_import(tmp_path: Path) -> None:
    """`@lc_tool` is not the text `@tool`; only the import graph knows."""
    sites = _find(
        tmp_path,
        "from langchain_core.tools import tool as lc_tool\n"
        "\n"
        "@lc_tool\n"
        "def search(q: str) -> str:\n"
        "    return q\n",
    )

    assert [s.detection for s in sites] == ["decorator:langchain_core.tools.tool"]


def test_finds_a_called_decorator_and_takes_its_explicit_name(tmp_path: Path) -> None:
    sites = _find(
        tmp_path,
        "from langchain_core.tools import tool\n"
        "\n"
        '@tool("web-search", description="Find things.")\n'
        "def search(q: str) -> str:\n"
        "    return q\n",
    )

    assert [(s.name, s.callable, s.description) for s in sites] == [
        ("web-search", "main.search", "Find things.")
    ]


def test_finds_an_attribute_decorator(tmp_path: Path) -> None:
    """`@mcp.tool()` and `@agent.tool` are attributes on a live object."""
    sites = _find(
        tmp_path,
        "from mcp.server.fastmcp import FastMCP\n"
        "\n"
        'mcp = FastMCP("demo")\n'
        "\n"
        "@mcp.tool()\n"
        "def add(a: int, b: int) -> int:\n"
        '    """Add numbers."""\n'
        "    return a + b\n",
    )

    assert [(s.name, s.detection) for s in sites] == [("add", "decorator:mcp.tool")]


def test_finds_a_kernel_function(tmp_path: Path) -> None:
    sites = _find(
        tmp_path,
        "from semantic_kernel.functions import kernel_function\n"
        "\n"
        "class Plugin:\n"
        '    @kernel_function(name="get_time", description="Clock.")\n'
        "    def now(self) -> str:\n"
        '        return "now"\n',
    )

    assert [(s.name, s.callable, s.description) for s in sites] == [
        ("get_time", "main.Plugin.now", "Clock.")
    ]
    # `self` is not a model-visible input.
    assert sites[0].inputs == []


def test_finds_a_function_tool_decorator(tmp_path: Path) -> None:
    sites = _find(
        tmp_path,
        "from agents import function_tool\n"
        "\n"
        "@function_tool\n"
        "def fetch(url: str) -> str:\n"
        "    return url\n",
    )

    assert [s.detection for s in sites] == ["decorator:agents.function_tool"]


# --- constructors ---------------------------------------------------------


def test_finds_structured_tool_from_function(tmp_path: Path) -> None:
    sites = _find(
        tmp_path,
        "from langchain_core.tools import StructuredTool\n"
        "\n"
        "def lookup(q: str) -> str:\n"
        '    """Look it up."""\n'
        "    return q\n"
        "\n"
        'st = StructuredTool.from_function(func=lookup, name="lookup_tool")\n',
    )

    assert [(s.name, s.callable, s.detection) for s in sites] == [
        ("lookup_tool", "main.lookup", "constructor:StructuredTool.from_function")
    ]
    # The signature comes from the referenced definition, not the call site.
    assert [(a.name, a.type) for a in sites[0].inputs] == [("q", "str")]
    assert sites[0].description == "Look it up."


def test_finds_a_plain_tool_constructor_with_a_positional_callable(tmp_path: Path) -> None:
    sites = _find(
        tmp_path,
        "from langchain.tools import Tool\n"
        "\n"
        "def run(q: str) -> str:\n"
        "    return q\n"
        "\n"
        't = Tool("search", run, description="Search.")\n',
    )

    assert [(s.name, s.callable, s.detection) for s in sites] == [
        ("search", "main.run", "constructor:Tool")
    ]


def test_finds_llamaindex_function_tool_from_defaults(tmp_path: Path) -> None:
    sites = _find(
        tmp_path,
        "from llama_index.core.tools import FunctionTool\n"
        "\n"
        "def multiply(a: int, b: int) -> int:\n"
        "    return a * b\n"
        "\n"
        "t = FunctionTool.from_defaults(fn=multiply)\n",
    )

    assert [(s.name, s.detection) for s in sites] == [
        ("multiply", "constructor:FunctionTool.from_defaults")
    ]


# --- tools=[...] wiring ---------------------------------------------------


def test_finds_functions_passed_in_a_tools_kwarg(tmp_path: Path) -> None:
    sites = _find(
        tmp_path,
        "from crewai import Agent\n"
        "\n"
        "def search(q: str) -> str:\n"
        '    """Search."""\n'
        "    return q\n"
        "\n"
        'a = Agent(role="R", tools=[search])\n',
    )

    assert [(s.name, s.callable, s.detection) for s in sites] == [
        ("search", "main.search", "tools_kwarg:Agent")
    ]


def test_finds_a_third_party_tool_object_it_cannot_introspect(tmp_path: Path) -> None:
    """smolagents' `tools=[DuckDuckGoSearchTool()]` is a tool we have no
    source for. Reported with empty inputs, never dropped.
    """
    sites = _find(
        tmp_path,
        "from smolagents import CodeAgent, DuckDuckGoSearchTool\n"
        "\n"
        "agent = CodeAgent(tools=[DuckDuckGoSearchTool()])\n",
    )

    assert [(s.name, s.callable, s.detection) for s in sites] == [
        (
            "DuckDuckGoSearchTool",
            "smolagents.DuckDuckGoSearchTool",
            "tools_kwarg:CodeAgent",
        )
    ]
    assert sites[0].inputs == []
    assert sites[0].description is None


def test_finds_tools_bound_positionally_to_bind_tools(tmp_path: Path) -> None:
    sites = _find(
        tmp_path,
        "def search(q: str) -> str:\n    return q\n\nmodel = llm.bind_tools([search])\n",
    )

    assert [(s.name, s.detection) for s in sites] == [("search", "tools_kwarg:bind_tools")]


def test_reports_an_unresolvable_tools_list_instead_of_dropping_it(tmp_path: Path) -> None:
    """`tools=get_tools()` hides the whole toolset behind a call. We cannot
    enumerate it, but a reviewer must still be told it is there.
    """
    sites = _find(tmp_path, "from crewai import Agent\na = Agent(tools=get_tools())\n")

    assert len(sites) == 1
    assert sites[0].detection == "tools_kwarg_unresolved:Agent"
    assert sites[0].callable == "get_tools()"


def test_finds_a_raw_openai_tool_schema(tmp_path: Path) -> None:
    sites = _find(
        tmp_path,
        "resp = client.chat.completions.create(\n"
        '    model="gpt-4o",\n'
        "    tools=[\n"
        "        {\n"
        '            "type": "function",\n'
        '            "function": {\n'
        '                "name": "get_weather",\n'
        '                "description": "Current weather.",\n'
        "            },\n"
        "        }\n"
        "    ],\n"
        ")\n",
    )

    assert [(s.name, s.description, s.detection) for s in sites] == [
        ("get_weather", "Current weather.", "tools_kwarg:create")
    ]


# --- invariants -----------------------------------------------------------


def test_deduplicates_a_tool_found_by_two_rules(tmp_path: Path) -> None:
    sites = _find(
        tmp_path,
        "from langchain_core.tools import tool\n"
        "from crewai import Agent\n"
        "\n"
        "@tool\n"
        "def search(q: str) -> str:\n"
        '    """Search."""\n'
        "    return q\n"
        "\n"
        "a = Agent(tools=[search])\n",
    )

    assert len(sites) == 1
    assert sites[0].detection == "decorator:langchain_core.tools.tool, tools_kwarg:Agent"
    # The richer, definition-derived facts survive the merge.
    assert sites[0].description == "Search."
    assert [(a.name, a.type) for a in sites[0].inputs] == [("q", "str")]


def test_an_unannotated_parameter_has_no_type_rather_than_a_default_one(tmp_path: Path) -> None:
    sites = _find(
        tmp_path,
        "from langchain_core.tools import tool\n"
        "\n"
        "@tool\n"
        "def search(q, limit: int = 3):\n"
        "    return q\n",
    )

    assert [(a.name, a.type) for a in sites[0].inputs] == [("q", None), ("limit", "int")]
    assert sites[0].outputs == []


def test_annotations_are_captured_as_written(tmp_path: Path) -> None:
    """Normalization to agent_spec types happens later; the probe must not
    pre-empt it.
    """
    sites = _find(
        tmp_path,
        "from langchain_core.tools import tool\n"
        "\n"
        "@tool\n"
        "def search(ids: list[int] | None = None) -> dict[str, str]:\n"
        "    return {}\n",
    )

    assert [(a.name, a.type) for a in sites[0].inputs] == [("ids", "list[int] | None")]
    assert [(a.name, a.type) for a in sites[0].outputs] == [("return", "dict[str, str]")]


def test_does_not_match_a_decorator_named_in_a_string(tmp_path: Path) -> None:
    assert _find(tmp_path, 'log("@tool def search(q: str) -> str")\n') == []


def test_does_not_flag_an_undecorated_function(tmp_path: Path) -> None:
    assert _find(tmp_path, "def helper(q: str) -> str:\n    return q\n") == []


def test_every_site_carries_provenance(tmp_path: Path) -> None:
    sites = _find(
        tmp_path,
        "from langchain_core.tools import tool\n"
        "\n"
        "@tool\n"
        "def search(q: str) -> str:\n"
        "    return q\n",
    )

    assert sites[0].site.file.endswith("main.py")
    assert sites[0].site.line == 4
    assert sites[0].site.node_id == "main.search"


def test_output_is_sorted_and_deterministic(tmp_path: Path) -> None:
    source = (
        "from langchain_core.tools import tool\n"
        "\n"
        "@tool\n"
        "def b(q: str) -> str:\n"
        "    return q\n"
        "\n"
        "@tool\n"
        "def a(q: str) -> str:\n"
        "    return q\n"
    )
    repo_graph = build_repo_graph(_repo(tmp_path, source))

    first = find_tool_sites(repo_graph)
    assert [s.name for s in first] == ["b", "a"]  # file/line order, not alphabetical
    assert first == find_tool_sites(repo_graph)


# --- the acceptance fixture ----------------------------------------------


def test_finds_the_research_agent_fixture_tool() -> None:
    sites = find_tool_sites(build_repo_graph(FIXTURES / "langgraph_research_agent"))

    assert len(sites) == 1
    site = sites[0]
    assert site.name == "web_search"
    assert site.callable == "tools.search.web_search"
    assert site.description == "Search the web for sources."
    assert [(a.name, a.type) for a in site.inputs] == [("query", "str"), ("limit", "int")]
    assert site.is_async is True
    assert site.detection == "decorator:langchain_core.tools.tool"
    assert site.site.file.endswith("tools/search.py")
