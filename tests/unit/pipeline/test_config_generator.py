"""Config generator tests — logic migration and rendering."""

from __future__ import annotations

from pathlib import Path

from superrobot.models.agent_config import AgentConfig, parse_signature_params
from superrobot.models.analysis_result import DrFramework
from superrobot.pipeline.config_generator import (
    flat_module_name,
    migrate_source_files,
    render_files,
)


def test_parse_signature_params() -> None:
    assert parse_signature_params("async def run_agent(query)") == ["query"]
    assert parse_signature_params("def run(self, query: str, k: int = 3)") == ["query", "k"]
    assert parse_signature_params("") == []


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text(
        "from tools.search import web_search\n\n"
        "async def run_agent(query):\n"
        "    return {'response': await web_search(query)}\n"
    )
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "search.py").write_text("async def web_search(q):\n    return q\n")
    return tmp_path


def test_migrate_source_files_flattens_and_rewrites_imports(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    migrated = migrate_source_files(repo)
    assert "agent/agent/main.py" in migrated
    assert "agent/agent/search.py" in migrated  # tools/search.py flattened
    # nested import rewritten to flat DRUM form
    assert "from search import web_search" in migrated["agent/agent/main.py"]
    assert "from tools.search" not in migrated["agent/agent/main.py"]


def test_migrate_skips_output_and_junk_dirs(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / ".superrobot").mkdir()
    (repo / ".superrobot" / "junk.py").write_text("x = 1\n")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "lib.py").write_text("x = 1\n")
    migrated = migrate_source_files(repo)
    assert not any("junk" in p or "lib" in p for p in migrated)


def test_render_files_wires_entry_point(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    config = AgentConfig(
        agent_purpose="test agent",
        dr_framework=DrFramework.LANGGRAPH,
        entry_file="main.py",
        entry_function="run_agent",
        entry_params=["query"],
        repo_path=str(repo),
        input_schema={"query": "str"},
        output_schema={"response": "str"},
    )
    files = render_files(config)
    myagent = files["agent/agent/myagent.py"]
    assert "from main import run_agent" in myagent
    assert "TODO" not in myagent
    assert "agent/agent/main.py" in files  # logic migrated into bundle
    assert "agent/agent/search.py" in files


def test_render_files_without_repo_keeps_todo() -> None:
    config = AgentConfig(dr_framework=DrFramework.LANGGRAPH)
    files = render_files(config)
    assert "TODO" in files["agent/agent/myagent.py"]


def test_flat_module_name(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    assert flat_module_name(repo, "main.py") == "main"
    assert flat_module_name(repo, "tools/search.py") == "search"


def test_llm_calls_rewired_to_gateway_shim(tmp_path: Path) -> None:
    """ChatOpenAI call sites route through dr_llm so they run on DR Gateway."""
    (tmp_path / "main.py").write_text(
        '"""Agent."""\n'
        "from langchain_openai import ChatOpenAI\n\n"
        "async def run_agent(query):\n"
        "    llm = ChatOpenAI(model='gpt-4o')\n"
        "    return {'response': str(await llm.ainvoke(query))}\n"
    )
    migrated = migrate_source_files(tmp_path)
    main = migrated["agent/agent/main.py"]
    assert "dr_chat_openai(model='gpt-4o')" in main
    assert "from dr_llm import dr_chat_openai" in main
    # import line inserted AFTER the module docstring
    assert main.startswith('"""Agent."""')
    # the original import survives untouched (shim imports lazily from it)
    assert "from langchain_openai import ChatOpenAI" in main


def test_llm_rewrite_does_not_touch_unrelated_names(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "class MyOpenAIHelper:\n    pass\n\ndef run(q):\n    return {'response': q}\n"
    )
    migrated = migrate_source_files(tmp_path)
    content = migrated["agent/agent/main.py"]
    assert "dr_openai" not in content
    assert "dr_llm" not in content


def test_bundle_includes_gateway_shim_when_migrated(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from openai import AsyncOpenAI\n\n"
        "async def run_agent(query):\n"
        "    client = AsyncOpenAI()\n"
        "    return {'response': query}\n"
    )
    config = AgentConfig(
        dr_framework=DrFramework.LANGGRAPH,
        entry_file="main.py",
        entry_function="run_agent",
        entry_params=["query"],
        repo_path=str(tmp_path),
    )
    files = render_files(config)
    assert "agent/agent/dr_llm.py" in files
    assert "genai/llmgw" in files["agent/agent/dr_llm.py"]
    assert "dr_async_openai(" in files["agent/agent/main.py"]


def test_render_files_includes_workload_service_and_manifest(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    config = AgentConfig(
        agent_name="research-agent",
        dr_framework=DrFramework.LANGGRAPH,
        entry_file="main.py",
        entry_function="run_agent",
        entry_params=["query"],
        repo_path=str(repo),
    )

    files = render_files(config)

    assert "workload/Dockerfile" in files
    assert "workload/workload.yaml" in files
    assert "agent/agent/workload_service.py" in files
    assert "/healthz" in files["agent/agent/workload_service.py"]
    assert "run_agent" in files["agent/agent/workload_service.py"]
    assert "replicaCount: 2" in files["workload/workload.yaml"]
