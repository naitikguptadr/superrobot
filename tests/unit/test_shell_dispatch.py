"""Tests for the bare/unrecognized-invocation -> shell dispatch decision."""

from __future__ import annotations

import pytest

from superrobot.cli import launch_shell, should_launch_shell


def test_bare_invocation_launches_shell() -> None:
    assert should_launch_shell([]) is True


def test_known_subcommands_do_not_launch_shell() -> None:
    for cmd in [
        "scan",
        "analyze",
        "generate",
        "transform",
        "validate",
        "deploy",
        "doctor",
        "setup",
        "status",
        "memory",
        "receipt",
    ]:
        assert should_launch_shell([cmd, "--json"]) is False, cmd


def test_known_global_flags_do_not_launch_shell() -> None:
    for flag in ["--help", "-h", "--version", "-V"]:
        assert should_launch_shell([flag]) is False, flag


def test_unrecognized_flags_launch_shell_for_passthrough() -> None:
    # e.g. `superrobot --print "some prompt"` should hand off to the shell,
    # not error out of Typer's own option parsing.
    assert should_launch_shell(["--print", "hello"]) is True


def test_a_free_text_prompt_launches_shell() -> None:
    assert should_launch_shell(["import this repo and deploy it"]) is True


def test_known_limitation_free_text_starting_with_a_subcommand_word_does_not_launch_shell() -> None:
    # Documents an accepted tradeoff: should_launch_shell only inspects
    # argv[0], so a prompt that happens to start with a subcommand word is
    # dispatched to Typer (which will report a usage error), not the shell.
    assert should_launch_shell(["generate", "a", "poem", "about", "pandas"]) is False


def test_launch_shell_exits_cleanly_when_shell_is_not_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("superrobot.cli.find_shell_entry", lambda: None)
    with pytest.raises(SystemExit) as exc:
        launch_shell([])
    assert exc.value.code == 1


def test_launch_shell_exits_cleanly_when_node_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("superrobot.cli.find_shell_entry", lambda: "/fake/dist/cli.js")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(SystemExit) as exc:
        launch_shell([])
    assert exc.value.code == 1


def test_launch_shell_exits_cleanly_when_execvp_raises_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("superrobot.cli.find_shell_entry", lambda: "/fake/dist/cli.js")
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/node")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("no such file or directory")

    monkeypatch.setattr("os.execvp", _boom)
    with pytest.raises(SystemExit) as exc:
        launch_shell([])
    assert exc.value.code == 1
