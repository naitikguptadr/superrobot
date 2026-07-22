"""Persist non-secret setup state and owner-only token file."""

from __future__ import annotations

import json
import os
from pathlib import Path

from superrobot.setup.models import SetupState

_DEFAULT_DIR = Path.home() / ".config" / "superrobot"


def config_dir(override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override)
    env = os.environ.get("SUPERROBOT_CONFIG_DIR", "").strip()
    return Path(env) if env else _DEFAULT_DIR


def ensure_config_dir(root: str | Path | None = None) -> Path:
    path = config_dir(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(root: str | Path | None = None) -> Path:
    return config_dir(root) / "setup.json"


def env_path(root: str | Path | None = None) -> Path:
    return config_dir(root) / ".env"


def save_state(state: SetupState, root: str | Path | None = None) -> Path:
    ensure_config_dir(root)
    destination = state_path(root)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n")
    temporary.replace(destination)
    return destination


def load_state(root: str | Path | None = None) -> SetupState | None:
    path = state_path(root)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid setup state at {path}")
    return SetupState.from_dict(payload)


def write_token_env(
    *,
    endpoint: str,
    token: str,
    model: str,
    root: str | Path | None = None,
) -> Path:
    """Write owner-only env file. Token is never written to setup.json."""
    ensure_config_dir(root)
    path = env_path(root)
    contents = (
        "# SuperRobot environment — do not commit\n"
        f"DATAROBOT_ENDPOINT={endpoint}\n"
        f"DATAROBOT_API_TOKEN={token}\n"
        f"SUPERROBOT_MODEL={model}\n"
    )
    path.write_text(contents)
    path.chmod(0o600)
    return path


def load_env_file(root: str | Path | None = None) -> dict[str, str]:
    path = env_path(root)
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", maxsplit=1)
        values[key] = value
    return values
