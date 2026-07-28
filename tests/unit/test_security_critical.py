"""Regression tests for three security defects (audit C19, C20, C21)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from superrobot.cli import app
from superrobot.models.receipt import Receipt
from superrobot.pipeline.receipts import load_receipt, save_receipt
from superrobot.shell_launcher import find_shell_entry

runner = CliRunner()


class TestShellLauncherDoesNotExecuteCodeFromTheCurrentDirectory:
    """C19 — resolution fell back to `Path.cwd()/shell/dist/cli.js`, which
    `cli.py` then os.execvp'd. Since this tool's entire job is ingesting
    untrusted third-party agent repos, a repo shipping that path got code
    execution the moment the user cd'd in and ran `superrobot` bare.
    """

    def test_cwd_is_not_searched(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        hostile = tmp_path / "untrusted-repo"
        (hostile / "shell" / "dist").mkdir(parents=True)
        (hostile / "shell" / "dist" / "cli.js").write_text("console.log('pwned')\n")
        monkeypatch.chdir(hostile)

        # A start path with no reachable shell/ of its own, mimicking a real
        # site-packages install.
        installed = tmp_path / "site-packages" / "superrobot" / "shell_launcher.py"
        installed.parent.mkdir(parents=True)
        installed.write_text("# stub")

        assert find_shell_entry(start=installed) is None

    def test_the_ancestor_walk_stops_at_a_repo_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stray `shell/dist/cli.js` in a parent directory (e.g. `~/shell/`
        for a pipx layout) must not be picked up either.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "shell" / "dist").mkdir(parents=True)
        (tmp_path / "shell" / "dist" / "cli.js").write_text("console.log('pwned')\n")

        nested = tmp_path / "a" / "b" / "superrobot" / "shell_launcher.py"
        nested.parent.mkdir(parents=True)
        nested.write_text("# stub")

        assert find_shell_entry(start=nested) is None

    def test_a_real_repo_checkout_still_resolves(self, tmp_path: Path) -> None:
        """The legitimate case: a checkout with pyproject.toml alongside
        shell/dist/cli.js must keep working.
        """
        repo = tmp_path / "checkout"
        (repo / "shell" / "dist").mkdir(parents=True)
        (repo / "shell" / "dist" / "cli.js").write_text("// real")
        (repo / "pyproject.toml").write_text("[project]\nname='superrobot'\n")
        pkg = repo / "superrobot" / "shell_launcher.py"
        pkg.parent.mkdir(parents=True)
        pkg.write_text("# stub")

        assert find_shell_entry(start=pkg) == repo / "shell" / "dist" / "cli.js"

    def test_the_explicit_env_override_still_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shell_dir = tmp_path / "explicit"
        (shell_dir / "dist").mkdir(parents=True)
        (shell_dir / "dist" / "cli.js").write_text("// real")
        monkeypatch.setenv("SUPERROBOT_SHELL_DIR", str(shell_dir))

        unrelated = tmp_path / "elsewhere" / "shell_launcher.py"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("# stub")

        assert find_shell_entry(start=unrelated) == shell_dir / "dist" / "cli.js"


class TestReceiptIdCannotEscapeTheReceiptsDirectory:
    """C20 — the receipt id was interpolated straight into a path. The read
    side is reachable from `receipt show|diagnose|replace` arguments.
    """

    @pytest.mark.parametrize(
        "hostile_id",
        ["../../secret", "../escape", "/etc/passwd", "a/../../b", "..", "sub/dir"],
    )
    def test_load_rejects_a_traversing_id(self, tmp_path: Path, hostile_id: str) -> None:
        outside = tmp_path / "secret.json"
        outside.write_text('{"nope": true}')

        with pytest.raises(ValueError):
            load_receipt(hostile_id, tmp_path / "cfg")

    def test_save_rejects_a_traversing_id(self, tmp_path: Path) -> None:
        receipt = Receipt(
            id="../../pwned",
            created_at="2026-01-01T00:00:00+00:00",
            target="workload",
            action="deployed",
            success=True,
            model="m",
        )

        with pytest.raises(ValueError):
            save_receipt(receipt, tmp_path / "cfg")

        assert not (tmp_path / "pwned.json").exists()

    def test_a_normal_id_round_trips(self, tmp_path: Path) -> None:
        receipt = Receipt(
            id="abc123def456",
            created_at="2026-01-01T00:00:00+00:00",
            target="workload",
            action="deployed",
            success=True,
            model="m",
        )
        save_receipt(receipt, tmp_path / "cfg")

        loaded = load_receipt("abc123def456", tmp_path / "cfg")

        assert loaded is not None
        assert loaded.id == "abc123def456"

    def test_a_missing_but_valid_id_is_still_just_none(self, tmp_path: Path) -> None:
        assert load_receipt("deadbeef0000", tmp_path / "cfg") is None


class TestJsonModeAlwaysEmitsJson:
    """C21 — error paths printed Rich text to stdout and returned before the
    json branch. The Pi shell parses that stdout as JSON, so users saw a
    parse failure instead of the actual error.
    """

    def _assert_stdout_is_json(self, output: str) -> dict:
        assert output.strip(), "expected a JSON payload on stdout"
        return json.loads(output)

    def test_validate_on_a_missing_path(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["validate", str(tmp_path / "nope"), "--json"])

        payload = self._assert_stdout_is_json(result.stdout)
        assert "error" in payload

    def test_memory_ensure_unauthenticated(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["memory", "ensure", "demo", "--config-dir", str(tmp_path), "--json"]
        )

        payload = self._assert_stdout_is_json(result.stdout)
        assert "error" in payload

    def test_deploy_with_an_unsupported_target(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["deploy", str(tmp_path), "--target", "bogus", "--json"])

        payload = self._assert_stdout_is_json(result.stdout)
        assert "error" in payload

    def test_deploy_workload_without_an_image_or_artifact(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "deploy",
                str(tmp_path),
                "--target",
                "workload",
                "--config-dir",
                str(tmp_path),
                "--json",
            ],
        )

        payload = self._assert_stdout_is_json(result.stdout)
        assert "error" in payload
