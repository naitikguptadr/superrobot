"""Tests for the libcst-based import rewriter -- format-preserving
replacement for ast_migrate.py's _ImportRewriter, which uses
ast.unparse() and can alter formatting/comments.
"""

from __future__ import annotations

from superrobot.pipeline.graph.migrate import rewrite_imports_libcst


def test_rewrites_nested_import_to_flat_name() -> None:
    source = "from tools.search import search\n"
    result, count = rewrite_imports_libcst(source, {"tools.search": "search"})

    assert count == 1
    assert result == "from search import search\n"


def test_preserves_comments_and_formatting() -> None:
    source = (
        "# this is a real dependency, do not remove\n"
        "from tools.search import search\n\n"
        "def run():\n"
        "    return search()\n"
    )
    result, count = rewrite_imports_libcst(source, {"tools.search": "search"})

    assert count == 1
    assert "# this is a real dependency, do not remove" in result
    assert "from search import search" in result
    assert "def run():" in result


def test_leaves_unrelated_imports_unchanged() -> None:
    source = "import os\nfrom typing import Any\n"
    result, count = rewrite_imports_libcst(source, {"tools.search": "search"})

    assert count == 0
    assert result == source


def test_does_not_corrupt_single_dot_relative_import() -> None:
    source = "from .search import search\n"
    result, count = rewrite_imports_libcst(source, {"search": "totally_unrelated_flat_module"})

    assert count == 0
    assert result == source


def test_does_not_corrupt_double_dot_relative_import() -> None:
    source = "from ..other import x\n"
    result, count = rewrite_imports_libcst(source, {"other": "totally_unrelated_flat_module"})

    assert count == 0
    assert result == source


def test_malformed_source_returns_unchanged_instead_of_raising() -> None:
    source = "def f(:\n    pass\n"
    result, count = rewrite_imports_libcst(source, {"tools.search": "search"})

    assert count == 0
    assert result == source


def test_import_x_dot_y_not_rewritten_known_limitation() -> None:
    source = "import tools.search\n"
    result, count = rewrite_imports_libcst(source, {"tools.search": "search"})

    assert count == 0
    assert result == source
