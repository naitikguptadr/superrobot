"""Tests for the bare/unrecognized-invocation -> shell dispatch decision."""

from __future__ import annotations

from superrobot.cli import should_launch_shell


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
