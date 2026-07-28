"""Locate the built Pi shell (shell/dist/cli.js) so `superrobot` with no
subcommand can launch it directly, instead of requiring a separate
`node shell/dist/cli.js` invocation."""

from __future__ import annotations

import os
from pathlib import Path

# A directory only counts as "the SuperRobot checkout" if it looks like a
# project root. Without this, the ancestor walk accepts any directory that
# merely happens to contain shell/dist/cli.js.
_REPO_MARKERS = ("pyproject.toml", "setup.py", ".git")


def find_shell_entry(start: Path | None = None) -> Path | None:
    """Return the path to shell/dist/cli.js, or None if it can't be found.

    Resolution order:
    1. SUPERROBOT_SHELL_DIR env var (a directory containing dist/cli.js)
    2. Walk up from this file's location for a sibling shell/dist/cli.js that
       sits in something recognizable as a project root

    The current working directory is deliberately NOT searched, and the
    ancestor walk requires a repo marker. `cli.py` hands this result straight
    to `os.execvp`, and this tool's whole purpose is ingesting untrusted
    third-party agent repos -- so a repo shipping `shell/dist/cli.js` used to
    get code execution as soon as the user cd'd into it and ran `superrobot`
    with no subcommand. The unmarked ancestor walk had the same problem one
    level up on pipx-style layouts, where a stray `~/shell/dist/cli.js` would
    win.

    Point SUPERROBOT_SHELL_DIR at a shell build to use one from elsewhere.
    """
    env_dir = os.environ.get("SUPERROBOT_SHELL_DIR")
    if env_dir:
        candidate = Path(env_dir) / "dist" / "cli.js"
        if candidate.is_file():
            return candidate

    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        candidate = parent / "shell" / "dist" / "cli.js"
        if candidate.is_file() and any((parent / marker).exists() for marker in _REPO_MARKERS):
            return candidate

    return None
