"""Deep AST migration tests."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.ast_migrate import (
    deep_migrate_source,
    detect_asyncio_gather,
    extract_hardcoded_prompts,
    rewrite_env_defaults,
    rewrite_imports_ast,
)
from superrobot.pipeline.config_generator import migrate_source_files_with_report


def test_rewrite_imports_ast_flattens_nested() -> None:
    src = "from tools.search import web_search\n\ndef run():\n    return web_search('q')\n"
    out, n = rewrite_imports_ast(src, {"tools.search": "search"})
    assert n >= 1
    assert "from search import web_search" in out
    assert "tools.search" not in out


def test_rewrite_imports_ast_relative() -> None:
    src = "from .search import web_search\n"
    out, n = rewrite_imports_ast(src, {"pkg.search": "search"})
    assert n >= 1
    assert "from search import web_search" in out
    assert "from ." not in out


def test_rewrite_env_defaults_strips_secret() -> None:
    src = "import os\nkey = os.getenv('OPENAI_API_KEY', 'sk-hardcoded')\n"
    out, n = rewrite_env_defaults(src)
    assert n == 1
    assert "sk-hardcoded" not in out
    assert "OPENAI_API_KEY" in out


def test_detect_asyncio_gather_warns() -> None:
    src = "import asyncio\nasync def run():\n    await asyncio.gather(a(), b())\n"
    count, notes = detect_asyncio_gather(src)
    assert count == 1
    assert any("sequentially" in n for n in notes)


def test_extract_hardcoded_prompts_marks() -> None:
    src = 'SYSTEM_PROMPT = "You are helpful."\n\ndef run():\n    return SYSTEM_PROMPT\n'
    out, n, notes = extract_hardcoded_prompts(src)
    assert n == 1
    assert "SUPERROBOT: hardcoded prompt" in out
    assert notes


def test_deep_migrate_combines_passes() -> None:
    src = (
        "from tools.search import web_search\n"
        "import os\n"
        "SYSTEM_PROMPT = 'hi'\n"
        "key = os.getenv('OPENAI_API_KEY', 'sk-x')\n"
    )
    out, report = deep_migrate_source(src, {"tools.search": "search"})
    assert report.flat_imports >= 1
    assert report.env_rewrites >= 1
    assert report.prompt_extractions >= 1
    assert "from search import" in out
    assert "sk-x" not in out


def test_migrate_source_files_deep_pass(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from tools.search import web_search\n"
        "import os\n"
        "SYSTEM_PROMPT = 'be helpful'\n"
        "async def run_agent(query):\n"
        "    key = os.getenv('OPENAI_API_KEY', 'sk-bad')\n"
        "    return {'response': await web_search(query)}\n"
    )
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "search.py").write_text("async def web_search(q):\n    return q\n")
    migrated, report = migrate_source_files_with_report(tmp_path)
    main = migrated["agent/agent/main.py"]
    assert "from search import web_search" in main
    assert "sk-bad" not in main
    assert "SUPERROBOT: hardcoded prompt" in main
    assert report.prompt_extractions >= 1
