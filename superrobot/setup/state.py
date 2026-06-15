"""Setup state persisted to ~/.config/superrobot/setup.yaml."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel

from superrobot.env import STATE_FILE, ensure_config_dir
from superrobot.setup.constants import DEFAULT_MODEL

SETUP_VERSION = "1"


class SetupState(BaseModel):
    """Persisted setup completion state."""

    version: str = SETUP_VERSION
    completed_at: str | None = None
    datarobot_endpoint: str = ""
    prerequisites_ok: bool = False
    auth_ok: bool = False
    gateway_ok: bool = False
    model: str = DEFAULT_MODEL

    @property
    def is_complete(self) -> bool:
        return (
            self.completed_at is not None
            and self.prerequisites_ok
            and self.auth_ok
            and self.gateway_ok
            and bool(self.datarobot_endpoint)
        )


def load_setup_state() -> SetupState:
    """Load setup state from disk, or return empty state."""
    if not STATE_FILE.exists():
        return SetupState()
    data = yaml.safe_load(STATE_FILE.read_text()) or {}
    return SetupState.model_validate(data)


def save_setup_state(state: SetupState) -> Path:
    """Persist setup state to disk."""
    ensure_config_dir()
    STATE_FILE.write_text(yaml.safe_dump(state.model_dump(), default_flow_style=False))
    return STATE_FILE


def is_setup_complete() -> bool:
    """Return True if setup has been completed successfully."""
    return load_setup_state().is_complete


def mark_setup_complete(
    endpoint: str,
    *,
    prerequisites_ok: bool = True,
    auth_ok: bool = True,
    gateway_ok: bool = True,
    model: str = DEFAULT_MODEL,
) -> SetupState:
    """Mark setup as complete and persist."""
    state = SetupState(
        completed_at=datetime.now(UTC).isoformat(),
        datarobot_endpoint=endpoint,
        prerequisites_ok=prerequisites_ok,
        auth_ok=auth_ok,
        gateway_ok=gateway_ok,
        model=model,
    )
    save_setup_state(state)
    return state
