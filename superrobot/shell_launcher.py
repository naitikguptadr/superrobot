"""Locate the built Pi shell (shell/dist/cli.js) so `superrobot` with no
subcommand can launch it directly, instead of requiring a separate
`node shell/dist/cli.js` invocation."""

from __future__ import annotations

import os
from pathlib import Path


def find_shell_entry(start: Path | None = None) -> Path | None:
    """Return the path to shell/dist/cli.js, or None if it can't be found.

    Resolution order:
    1. SUPERROBOT_SHELL_DIR env var (a directory containing dist/cli.js)
    2. Walk up from this file's location looking for a sibling shell/dist/cli.js
       (covers both editable installs and `uv run` from a repo checkout)
    3. shell/dist/cli.js relative to the current working directory
    """
    env_dir = os.environ.get("SUPERROBOT_SHELL_DIR")
    if env_dir:
        candidate = Path(env_dir) / "dist" / "cli.js"
        if candidate.is_file():
            return candidate

    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        candidate = parent / "shell" / "dist" / "cli.js"
        if candidate.is_file():
            return candidate

    cwd_candidate = Path.cwd() / "shell" / "dist" / "cli.js"
    if cwd_candidate.is_file():
        return cwd_candidate

    return None
