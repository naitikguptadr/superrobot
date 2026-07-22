"""Doctor — aggregate readiness checks."""

from __future__ import annotations

import os
import shutil

from superrobot.setup.config import load_env_file, load_state
from superrobot.setup.endpoints import EndpointError, api_endpoint, normalize_endpoint
from superrobot.setup.gateway import GatewayError, verify_gateway
from superrobot.setup.models import AuthMethod, DoctorResult, SetupState
from superrobot.setup.probes import check_dr_auth, probe_capabilities


async def run_doctor(
    *,
    config_root: str | None = None,
    skip_gateway: bool = False,
    token_override: str | None = None,
    endpoint_override: str | None = None,
) -> DoctorResult:
    checks: list[tuple[str, bool, str]] = []
    env = load_env_file(config_root)
    state = load_state(config_root)

    endpoint_raw = (
        endpoint_override
        or env.get("DATAROBOT_ENDPOINT")
        or (state.endpoint if state else "")
        or ("" if config_root is not None else os.environ.get("DATAROBOT_ENDPOINT", ""))
    )
    token = (
        token_override
        or env.get("DATAROBOT_API_TOKEN")
        or ("" if config_root is not None else os.environ.get("DATAROBOT_API_TOKEN", ""))
    )

    try:
        endpoint = normalize_endpoint(endpoint_raw) if endpoint_raw else ""
        if endpoint:
            checks.append(("endpoint", True, api_endpoint(endpoint)))
        else:
            checks.append(("endpoint", False, "DATAROBOT_ENDPOINT not set — run superrobot setup"))
    except EndpointError as exc:
        endpoint = ""
        checks.append(("endpoint", False, str(exc)))

    dr_probe = await check_dr_auth()
    if dr_probe.ok:
        checks.append(("auth", True, dr_probe.detail))
        auth_method = AuthMethod.DR_CLI
    elif token:
        checks.append(("auth", True, "API token present"))
        auth_method = AuthMethod.API_TOKEN
    else:
        checks.append(("auth", False, "Not authenticated — run superrobot setup or dr auth login"))
        auth_method = AuthMethod.NONE

    if shutil.which("dr"):
        checks.append(("dr_cli", True, "dr found on PATH"))
    else:
        checks.append(("dr_cli", False, "dr CLI missing — https://docs.datarobot.com"))

    if endpoint and token and not skip_gateway:
        try:
            await verify_gateway(endpoint, token, model=(state.model if state else None))
            checks.append(("llm_gateway", True, "Gateway verified"))
        except GatewayError as exc:
            checks.append(("llm_gateway", False, str(exc)))
    elif skip_gateway:
        checks.append(("llm_gateway", True, "Skipped"))
    else:
        checks.append(("llm_gateway", False, "Cannot verify Gateway without endpoint and token"))

    capabilities = state.capabilities if state else None
    if endpoint and token:
        capabilities = await probe_capabilities(endpoint, token)
        checks.append(
            (
                "capabilities",
                True,
                (
                    f"workload={'yes' if capabilities.workload else 'no'} "
                    f"memory={'yes' if capabilities.memory else 'no'} "
                    f"agent_app={'yes' if capabilities.agent_app else 'no'}"
                ),
            )
        )

    named = {name: ok for name, ok, _ in checks}
    ready = bool(named.get("endpoint") and named.get("auth") and named.get("llm_gateway"))

    snapshot = None
    if endpoint and auth_method is not AuthMethod.NONE and capabilities is not None:
        snapshot = SetupState(
            endpoint=endpoint,
            auth_method=auth_method,
            capabilities=capabilities,
            model=(state.model if state else "azure/gpt-5-5-2026-04-23"),
        )

    return DoctorResult(ready=ready, checks=tuple(checks), state=snapshot)
