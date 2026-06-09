"""Post-deploy utilities — README badge injection."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import quote

BADGE_BLOCK = """\
## Deploy

[![Deploy to DataRobot](https://app.datarobot.com/assets/deploy-badge.svg)](https://app.datarobot.com/deploy?repo={repo_url})

### Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
{env_rows}
| DATAROBOT_ENDPOINT | auto-set | Injected by DataRobot |
"""


def generate_readme_badge_diff(
    readme_path: Path,
    env_vars: list[str],
    repo_url: str = "",
) -> tuple[str, str]:
    """Return (original, proposed) README content with deploy badge section."""
    original = readme_path.read_text() if readme_path.exists() else ""
    env_rows = "\n".join(
        f"| {var} | Yes | Required for agent runtime |" for var in sorted(env_vars)
    )
    block = BADGE_BLOCK.format(repo_url=quote(repo_url, safe=""), env_rows=env_rows)
    if "## Deploy" in original:
        proposed = re.sub(r"## Deploy.*", block.rstrip(), original, flags=re.DOTALL)
    else:
        proposed = original.rstrip() + "\n\n" + block
    return original, proposed


def get_git_remote_url(repo_path: Path) -> str | None:
    """Get origin remote URL if available."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def apply_readme_badge(readme_path: Path, proposed: str) -> None:
    """Write proposed README content."""
    readme_path.write_text(proposed)
