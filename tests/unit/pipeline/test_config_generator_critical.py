"""Regression tests for two critical config_generator defects.

Both fire on the default happy path (see
docs/superpowers/reviews/2026-07-27-backend-audit.md, C1 and C3).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from superrobot.pipeline.config_generator import (
    _merge_dependencies,
    _requirement_name,
    write_generated_files,
)

# A perfectly ordinary PEP 621 pyproject. The multi-line `dependencies`
# array is the shape essentially every real project uses.
ORDINARY_PYPROJECT = """\
[project]
name = "ticket-triage"
version = "1.2.3"
description = "A support agent"
requires-python = ">=3.11"
dependencies = [
    "langgraph>=0.2.0",
    "langchain-openai>=0.2",
    "httpx",
]

[tool.ruff]
line-length = 100
"""


class TestMergeDependencies:
    """C1 — every line of the pyproject was being treated as a package name."""

    def test_only_real_packages_are_merged(self) -> None:
        merged = _merge_dependencies(["openai"], ORDINARY_PYPROJECT)
        names = {_requirement_name(dep) for dep in merged}

        assert {"langgraph", "langchain-openai", "httpx", "openai"} <= names

    def test_version_pins_from_the_original_are_preserved(self) -> None:
        """The user's constraints should reach the generated package rather
        than being flattened to a bare distribution name.
        """
        merged = _merge_dependencies(["langgraph"], ORDINARY_PYPROJECT)

        assert "langgraph>=0.2.0" in merged
        assert "langgraph" not in merged, "bare name duplicates the pinned entry"

    def test_does_not_emit_toml_syntax_as_packages(self) -> None:
        """The bug: a multi-line array's closing bracket, table headers, and
        `key = value` metadata lines all became "dependencies".
        """
        merged = _merge_dependencies([], ORDINARY_PYPROJECT)

        assert "]" not in merged
        assert "dependencies = [" not in merged
        for dep in merged:
            assert "=" not in dep.replace(">=", "").replace("==", ""), (
                f"{dep!r} is TOML syntax, not a package name"
            )
            assert not dep.startswith(("[", "]")), f"{dep!r} is TOML syntax"

    def test_does_not_leak_project_metadata_as_packages(self) -> None:
        merged = _merge_dependencies([], ORDINARY_PYPROJECT)
        joined = " ".join(merged)

        for leaked in ("ticket-triage", "1.2.3", "A support agent", "line-length"):
            assert leaked not in joined, f"project metadata {leaked!r} leaked into deps"

    def test_generated_dependencies_survive_a_toml_round_trip(self) -> None:
        """The downstream consequence: a bare `]` in the array truncated
        platform_rules._extract_dependencies, so validate_pyproject reported
        every original package as removed and aborted the migration.
        """
        merged = _merge_dependencies([], ORDINARY_PYPROJECT)
        rendered = "[project]\ndependencies = [\n"
        rendered += "".join(f'    "{dep}",\n' for dep in merged)
        rendered += "]\n"

        parsed = tomllib.loads(rendered)
        assert set(parsed["project"]["dependencies"]) == set(merged)

    def test_handles_a_single_line_dependencies_array(self) -> None:
        merged = _merge_dependencies([], '[project]\ndependencies = ["crewai>=0.30", "openai"]\n')
        names = {_requirement_name(dep) for dep in merged}

        assert {"crewai", "openai"} <= names
        assert "]" not in merged

    def test_handles_a_poetry_style_pyproject(self) -> None:
        """Poetry declares deps as a table; the old line scanner produced
        nothing usable, so the generated bundle shipped without the framework.
        """
        poetry = '[tool.poetry.dependencies]\npython = "^3.11"\ncrewai = "^0.30"\nhttpx = "*"\n'
        names = {_requirement_name(dep) for dep in _merge_dependencies([], poetry)}

        assert {"crewai", "httpx"} <= names
        assert "python" not in names, "the interpreter pin is not a distribution"

    def test_empty_original_pyproject_still_yields_base_requirements(self) -> None:
        assert _merge_dependencies([], "") == _merge_dependencies([], "")
        assert len(_merge_dependencies([], "")) > 0


class TestWriteGeneratedFilesDoesNotClobber:
    """C3 — writing generated files destroyed whatever was already there."""

    def test_refuses_to_overwrite_an_existing_file_by_default(self, tmp_path: Path) -> None:
        victim = tmp_path / "pyproject.toml"
        victim.write_text('[project]\nname = "user-project"\nversion = "9.9.9"\n')

        with pytest.raises(FileExistsError):
            write_generated_files({"pyproject.toml": "GENERATED"}, tmp_path)

        assert "user-project" in victim.read_text(), "the user's file was destroyed"

    def test_force_allows_the_overwrite(self, tmp_path: Path) -> None:
        victim = tmp_path / "pyproject.toml"
        victim.write_text("ORIGINAL")

        write_generated_files({"pyproject.toml": "GENERATED"}, tmp_path, force=True)

        assert victim.read_text() == "GENERATED"

    def test_writing_into_an_empty_directory_is_unaffected(self, tmp_path: Path) -> None:
        out = write_generated_files({"a/b.py": "x = 1\n"}, tmp_path)

        assert (out / "a" / "b.py").read_text() == "x = 1\n"

    def test_untouched_files_are_left_alone_when_the_write_is_refused(self, tmp_path: Path) -> None:
        """A refusal must not leave a half-written package behind."""
        (tmp_path / "pyproject.toml").write_text("ORIGINAL")

        with pytest.raises(FileExistsError):
            write_generated_files(
                {"brand_new.py": "x = 1\n", "pyproject.toml": "GENERATED"},
                tmp_path,
            )

        assert not (tmp_path / "brand_new.py").exists(), (
            "partial write: refused overall but still created files"
        )
