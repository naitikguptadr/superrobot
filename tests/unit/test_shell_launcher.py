"""Tests for shell entrypoint discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from superrobot.shell_launcher import find_shell_entry


def test_finds_shell_entry_by_walking_up_from_a_nested_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    nested_source = repo_root / "superrobot" / "cli.py"
    nested_source.parent.mkdir(parents=True)
    nested_source.write_text("# stub")
    shell_entry = repo_root / "shell" / "dist" / "cli.js"
    shell_entry.parent.mkdir(parents=True)
    shell_entry.write_text("// stub")
    # The ancestor walk only accepts a directory that looks like a project
    # root, so an untrusted repo that merely contains shell/dist/cli.js can't
    # hijack the launcher (see find_shell_entry / audit C19).
    (repo_root / "pyproject.toml").write_text("[project]\nname='superrobot'\n")

    found = find_shell_entry(start=nested_source)
    assert found == shell_entry


def test_returns_none_when_no_shell_entry_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested_source = tmp_path / "somewhere" / "cli.py"
    nested_source.parent.mkdir(parents=True)
    nested_source.write_text("# stub")
    # Isolate from the real repo's cwd -- otherwise the cwd-relative fallback
    # would find this actual project's real shell/dist/cli.js.
    monkeypatch.chdir(tmp_path)

    assert find_shell_entry(start=nested_source) is None


def test_env_var_override_takes_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shell_dir = tmp_path / "custom-shell-location"
    (shell_dir / "dist").mkdir(parents=True)
    shell_entry = shell_dir / "dist" / "cli.js"
    shell_entry.write_text("// stub")
    monkeypatch.setenv("SUPERROBOT_SHELL_DIR", str(shell_dir))

    # Even with a start path that has no reachable shell/, the env var should win.
    unrelated = tmp_path / "unrelated" / "cli.py"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("# stub")

    assert find_shell_entry(start=unrelated) == shell_entry
