"""Environment-configuration discovery.

The valuable output is `consumers` -- which callable an env var reaches --
because that is what makes a credential migratable. An env var we cannot
trace is reported with no consumers, never omitted.
"""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.graph.builder import build_repo_graph
from superrobot.pipeline.probes.config import find_config_sites

FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures"


def _repo(tmp_path: Path, source: str) -> Path:
    (tmp_path / "main.py").write_text(source)
    return tmp_path


def test_langchain_fixture_traces_the_key_into_the_client() -> None:
    """The acceptance case: OPENAI_API_KEY reaches ChatOpenAI."""
    sites = find_config_sites(build_repo_graph(FIXTURES / "langchain_agent"))

    key = next(s for s in sites if s.name == "OPENAI_API_KEY" and s.site.file.endswith("main.py"))
    assert any(consumer.endswith("ChatOpenAI") for consumer in key.consumers)
    assert key.access == "os.getenv"
    assert key.required is False
    assert key.default == "''"


def test_subscript_access_is_required(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'import os\n\nKEY = os.environ["API_KEY"]\n')

    sites = find_config_sites(build_repo_graph(repo))

    assert [(s.name, s.access, s.required, s.default) for s in sites] == [
        ("API_KEY", "os.environ[]", True, None)
    ]


def test_environ_get_with_a_default(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'import os\n\nMODE = os.environ.get("MODE", "dev")\n')

    site = find_config_sites(build_repo_graph(repo))[0]

    assert (site.name, site.access, site.required, site.default) == (
        "MODE",
        "os.environ.get",
        False,
        "'dev'",
    )


def test_getenv_without_a_default_is_required(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'import os\n\nKEY = os.getenv("API_KEY")\n')

    site = find_config_sites(build_repo_graph(repo))[0]

    assert (site.access, site.required, site.default) == ("os.getenv", True, None)


def test_resolves_through_import_aliases(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "import os as _os\nfrom os import getenv, environ\n\n"
        'A = _os.getenv("A")\nB = getenv("B")\nC = environ["C"]\n',
    )

    sites = find_config_sites(build_repo_graph(repo))

    assert [(s.name, s.access) for s in sites] == [
        ("A", "os.getenv"),
        ("B", "os.getenv"),
        ("C", "os.environ[]"),
    ]


def test_a_non_literal_name_is_reported_not_dropped(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "import os\n\n\ndef f(var):\n    return os.getenv(var)\n")

    sites = find_config_sites(build_repo_graph(repo))

    assert len(sites) == 1
    assert "unresolved" in sites[0].name
    assert "var" in sites[0].name


def test_consumers_include_a_direct_inline_argument(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "import os\nfrom langchain_openai import ChatOpenAI\n\n"
        'llm = ChatOpenAI(api_key=os.environ["OPENAI_API_KEY"])\n',
    )

    site = find_config_sites(build_repo_graph(repo))[0]

    assert site.consumers == ["langchain_openai.ChatOpenAI"]


def test_consumers_follow_an_assignment_inside_a_function(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "import os\nfrom langchain_openai import ChatOpenAI\n\n\n"
        "def build():\n"
        '    key = os.getenv("OPENAI_API_KEY")\n'
        "    return ChatOpenAI(api_key=key)\n",
    )

    site = find_config_sites(build_repo_graph(repo))[0]

    assert site.consumers == ["langchain_openai.ChatOpenAI"]


def test_consumers_stop_at_the_object_the_value_was_built_into(tmp_path: Path) -> None:
    """`agent` holds an Agent, not the key -- attributing `agent`'s later
    uses to the key would report consumers the value never reaches.
    """
    repo = _repo(
        tmp_path,
        "import os\nfrom autogen import AssistantAgent\n\n"
        'agent = AssistantAgent(llm_config={"api_key": os.getenv("OPENAI_API_KEY")})\n'
        "agent.initiate_chat(message='hi')\n",
    )

    site = find_config_sites(build_repo_graph(repo))[0]

    assert site.consumers == ["autogen.AssistantAgent"]


def test_an_untraceable_value_is_reported_with_no_consumers(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'import os\n\nDEBUG = os.getenv("DEBUG")\n')

    site = find_config_sites(build_repo_graph(repo))[0]

    assert site.name == "DEBUG"
    assert site.consumers == []


def test_pydantic_settings_fields_are_config(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "from pydantic_settings import BaseSettings\n\n\n"
        "class Settings(BaseSettings):\n"
        "    openai_api_key: str\n"
        '    log_level: str = "info"\n',
    )

    sites = find_config_sites(build_repo_graph(repo))

    assert [(s.name, s.required, s.default, s.access) for s in sites] == [
        ("OPENAI_API_KEY", True, None, "pydantic_settings"),
        ("LOG_LEVEL", False, "'info'", "pydantic_settings"),
    ]


def test_pydantic_settings_env_prefix_is_applied(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "from pydantic_settings import BaseSettings, SettingsConfigDict\n\n\n"
        "class Settings(BaseSettings):\n"
        '    model_config = SettingsConfigDict(env_prefix="app_")\n'
        "    token: str\n",
    )

    sites = find_config_sites(build_repo_graph(repo))

    assert [s.name for s in sites] == ["APP_TOKEN"]


def test_string_literals_are_not_env_reads(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "note = \"we read os.environ['X'] somewhere\"\n")

    assert find_config_sites(build_repo_graph(repo)) == []


def test_every_site_carries_provenance(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'import os\n\nKEY = os.environ["API_KEY"]\n')

    site = find_config_sites(build_repo_graph(repo))[0]

    assert site.site.file.endswith("main.py")
    assert site.site.line == 3
