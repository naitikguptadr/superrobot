"""Wrappers around DataRobot's own scaffold scripts.

The architecture's second core decision is that we own only the frontend:
the scaffold is DataRobot's, and their scripts are *called*, never
reimplemented. These tests pin the calling convention and the drift guard,
and deliberately never touch a real subprocess -- `clone_template.py` runs
`git clone` against the network and `setup_template.py` shells out to `dr`
and Pulumi.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from superrobot.dr.scaffold import (
    FRAMEWORKS,
    ScaffoldError,
    ScriptResult,
    clone_template,
    select_framework,
    setup_template,
    skill_scripts_dir,
)


class FakeRunner:
    """Records invocations instead of running them."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self._returncode = returncode
        self._stderr = stderr

    def __call__(self, command: list[str], *, cwd: Path) -> ScriptResult:
        self.calls.append(list(command))
        return ScriptResult(returncode=self._returncode, stdout="", stderr=self._stderr)


@pytest.fixture
def scripts_dir(tmp_path: Path) -> Path:
    """A stand-in for the vendored scripts directory."""
    d = tmp_path / "scripts"
    d.mkdir()
    for name in ("clone_template.py", "select_framework.py", "setup_template.py"):
        (d / name).write_text("# stub")
    return d


@pytest.fixture
def target(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


class TestTheCallingConvention:
    def test_clone_template_invokes_the_real_script(self, scripts_dir: Path, target: Path) -> None:
        runner = FakeRunner()

        clone_template(target, runner=runner, scripts_dir=scripts_dir)

        (command,) = runner.calls
        assert command[0] == sys.executable
        assert command[1] == str(scripts_dir / "clone_template.py")
        assert command[2:] == ["--target-dir", str(target.resolve())]

    def test_select_framework_passes_the_framework_and_target(
        self, scripts_dir: Path, target: Path
    ) -> None:
        runner = FakeRunner()

        select_framework(target, "langgraph", runner=runner, scripts_dir=scripts_dir)

        (command,) = runner.calls
        assert command[1] == str(scripts_dir / "select_framework.py")
        assert command[2:] == [
            "--framework",
            "langgraph",
            "--target-dir",
            str(target.resolve()),
        ]

    def test_setup_template_passes_the_llm_model(self, scripts_dir: Path, target: Path) -> None:
        runner = FakeRunner()

        setup_template(target, "openai/gpt-4o", runner=runner, scripts_dir=scripts_dir)

        (command,) = runner.calls
        assert command[1] == str(scripts_dir / "setup_template.py")
        assert command[2:] == [
            "--llm-model",
            "openai/gpt-4o",
            "--target-dir",
            str(target.resolve()),
        ]


class TestFailuresSurfaceRatherThanPassSilently:
    def test_a_non_zero_exit_raises_with_the_script_stderr(
        self, scripts_dir: Path, target: Path
    ) -> None:
        """A scaffold step that failed but returned normally would let the
        pipeline build on a directory that is not a DataRobot recipe.
        """
        runner = FakeRunner(returncode=1, stderr="fatal: repository not found")

        with pytest.raises(ScaffoldError) as excinfo:
            clone_template(target, runner=runner, scripts_dir=scripts_dir)

        assert "fatal: repository not found" in str(excinfo.value)
        assert "clone_template.py" in str(excinfo.value)

    def test_a_missing_target_directory_is_rejected_before_running_anything(
        self, scripts_dir: Path, tmp_path: Path
    ) -> None:
        runner = FakeRunner()

        with pytest.raises(ScaffoldError):
            clone_template(tmp_path / "nope", runner=runner, scripts_dir=scripts_dir)

        assert runner.calls == []

    def test_a_missing_script_is_rejected_before_running_anything(
        self, tmp_path: Path, target: Path
    ) -> None:
        empty = tmp_path / "empty-scripts"
        empty.mkdir()
        runner = FakeRunner()

        with pytest.raises(ScaffoldError) as excinfo:
            clone_template(target, runner=runner, scripts_dir=empty)

        assert runner.calls == []
        assert "clone_template.py" in str(excinfo.value)

    def test_an_unsupported_framework_is_rejected_before_running_anything(
        self, scripts_dir: Path, target: Path
    ) -> None:
        runner = FakeRunner()

        with pytest.raises(ScaffoldError) as excinfo:
            select_framework(target, "autogen", runner=runner, scripts_dir=scripts_dir)

        assert runner.calls == []
        assert "autogen" in str(excinfo.value)
        assert "langgraph" in str(excinfo.value), "the error should name what IS supported"


class TestTheDriftGuard:
    """The spec names a moving upstream contract as the top risk. These
    assertions fail loudly when the vendored scripts change under us,
    instead of us discovering it at deploy time.
    """

    def test_the_vendored_scripts_directory_resolves(self) -> None:
        resolved = skill_scripts_dir()

        assert resolved.is_dir()
        assert (resolved / "clone_template.py").is_file()
        assert (resolved / "select_framework.py").is_file()
        assert (resolved / "setup_template.py").is_file()

    def test_our_framework_list_matches_the_vendored_script(self) -> None:
        source = (skill_scripts_dir() / "select_framework.py").read_text()
        match = re.search(r"^FRAMEWORKS\s*=\s*\[(.*?)\]", source, re.MULTILINE | re.DOTALL)
        assert match, "select_framework.py no longer declares FRAMEWORKS as a list literal"

        upstream = re.findall(r'"([^"]+)"', match.group(1))

        assert list(FRAMEWORKS) == upstream
