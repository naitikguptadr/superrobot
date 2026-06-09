"""Setup verification checks."""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass, field

from superrobot.dr.cli_wrapper import DrCliWrapper
from superrobot.startup import REQUIRED_BINARIES, _install_hint


@dataclass
class PrerequisiteStatus:
    """Status of a single prerequisite binary."""

    name: str
    installed: bool
    install_hint: str = ""


@dataclass
class SetupCheckResult:
    """Aggregate result of all setup checks."""

    prerequisites: list[PrerequisiteStatus] = field(default_factory=list)
    auth_ok: bool = False
    endpoint_set: bool = False
    token_set: bool = False
    gateway_ok: bool = False
    gateway_error: str | None = None

    @property
    def prerequisites_ok(self) -> bool:
        return all(p.installed for p in self.prerequisites)

    @property
    def env_ok(self) -> bool:
        return self.endpoint_set and self.token_set

    @property
    def is_ready(self) -> bool:
        return self.prerequisites_ok and self.auth_ok and self.env_ok and self.gateway_ok


def check_prerequisites() -> list[PrerequisiteStatus]:
    """Check all required binaries."""
    system = platform.system()
    return [
        PrerequisiteStatus(
            name=binary,
            installed=shutil.which(binary) is not None,
            install_hint=_install_hint(binary) if system else "",
        )
        for binary in REQUIRED_BINARIES
    ]


async def check_auth(cli: DrCliWrapper | None = None) -> bool:
    """Check dr CLI authentication."""
    wrapper = cli or DrCliWrapper()
    return await wrapper.auth_check()


def check_env_vars() -> tuple[bool, bool]:
    """Check DATAROBOT_ENDPOINT and DATAROBOT_API_TOKEN are set."""
    endpoint = os.environ.get("DATAROBOT_ENDPOINT", "").strip()
    token = os.environ.get("DATAROBOT_API_TOKEN", "").strip()
    return bool(endpoint), bool(token)


async def check_gateway() -> tuple[bool, str | None]:
    """Verify LLM Gateway connectivity with a minimal call."""
    endpoint_set, token_set = check_env_vars()
    endpoint = os.environ.get("DATAROBOT_ENDPOINT", "")
    if not endpoint_set or not token_set:
        return False, "DATAROBOT_ENDPOINT and DATAROBOT_API_TOKEN must be set"

    if "prediction" in endpoint.lower() and "api" in endpoint.lower():
        return False, "Use Platform API URL (DATAROBOT_ENDPOINT), not prediction URL"

    try:
        from superrobot.dr.llm_gateway import LLMGateway

        gw = LLMGateway()
        # Minimal ping via raw chat without JSON mode
        result = await gw.ping()
        if result:
            return True, None
        return False, "Gateway returned empty response"
    except Exception as exc:
        return False, str(exc)


async def run_all_checks(cli: DrCliWrapper | None = None) -> SetupCheckResult:
    """Run all setup checks and return aggregate result."""
    prereqs = check_prerequisites()
    auth_ok = await check_auth(cli) if all(p.installed for p in prereqs) else False
    endpoint_set, token_set = check_env_vars()
    gateway_ok, gateway_error = (False, None)
    if endpoint_set and token_set:
        gateway_ok, gateway_error = await check_gateway()

    return SetupCheckResult(
        prerequisites=prereqs,
        auth_ok=auth_ok,
        endpoint_set=endpoint_set,
        token_set=token_set,
        gateway_ok=gateway_ok,
        gateway_error=gateway_error,
    )
