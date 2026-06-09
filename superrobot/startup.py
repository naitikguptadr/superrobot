"""Prerequisites check and dr auth validation (pre-TUI)."""

from __future__ import annotations

import platform
import shutil
import sys

from superrobot.env import load_user_env

load_user_env()

REQUIRED_BINARIES = ["dr", "uv", "task", "pulumi", "node", "npm"]

_INSTALL_HINTS: dict[str, dict[str, str]] = {
    "dr": {
        "Darwin": "brew install datarobot/dr/dr",
        "Linux": "See https://docs.datarobot.com for dr CLI install",
        "Windows": "Use WSL or Codespaces (BUZZOK-29366)",
    },
    "uv": {
        "Darwin": "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "Linux": "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "Windows": 'powershell -c "irm https://astral.sh/uv/install.ps1 | iex"',
    },
    "task": {
        "Darwin": "brew install go-task",
        "Linux": 'sh -c "$(curl --location https://taskfile.dev/install.sh)"',
        "Windows": "choco install go-task",
    },
    "pulumi": {
        "Darwin": "brew install pulumi",
        "Linux": "curl -fsSL https://get.pulumi.com | sh",
        "Windows": "choco install pulumi",
    },
    "node": {
        "Darwin": "brew install node",
        "Linux": "https://nodejs.org/en/download",
        "Windows": "https://nodejs.org/en/download",
    },
    "npm": {
        "Darwin": "Included with node",
        "Linux": "Included with node",
        "Windows": "Included with node",
    },
}


def _install_hint(binary: str) -> str:
    system = platform.system()
    hints = _INSTALL_HINTS.get(binary, {})
    return hints.get(system, hints.get("Linux", f"Install {binary}"))


def check_prerequisites() -> list[str]:
    """Return list of missing required binaries."""
    return [b for b in REQUIRED_BINARIES if shutil.which(b) is None]


def print_missing_prerequisites(missing: list[str]) -> None:
    """Print OS-specific install instructions and exit."""
    system = platform.system()
    print(f"SuperRobot prerequisites check failed ({system}):\n", file=sys.stderr)
    for binary in missing:
        print(f"  ✗ {binary} — {_install_hint(binary)}", file=sys.stderr)
    if system == "Windows":
        print(
            "\nWindows is not natively supported (BUZZOK-29366). Use Codespaces or WSL.",
            file=sys.stderr,
        )
    sys.exit(1)


async def check_auth() -> bool:
    """Delegate auth check to dr CLI."""
    from superrobot.dr.cli_wrapper import DrCliWrapper

    wrapper = DrCliWrapper()
    return await wrapper.auth_check()
