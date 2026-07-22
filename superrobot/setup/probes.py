"""Auth and platform capability probes."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from superrobot.setup.endpoints import api_endpoint
from superrobot.setup.models import AuthMethod, CapabilityMatrix


@dataclass(frozen=True)
class AuthProbe:
    method: AuthMethod
    ok: bool
    detail: str


async def check_dr_auth(dr_binary: str = "dr") -> AuthProbe:
    """Prefer dr CLI auth when the binary exists."""
    if shutil.which(dr_binary) is None:
        return AuthProbe(AuthMethod.NONE, False, "dr CLI not found on PATH")
    proc = await asyncio.create_subprocess_exec(
        dr_binary,
        "auth",
        "check",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    if proc.returncode == 0:
        return AuthProbe(AuthMethod.DR_CLI, True, "dr auth check passed")
    detail = (stderr_b or stdout_b).decode().strip() or f"exit {proc.returncode}"
    return AuthProbe(AuthMethod.DR_CLI, False, detail)


async def probe_capabilities(
    endpoint: str,
    token: str,
    *,
    get: Callable[[str, dict[str, str]], Awaitable[tuple[int, object]]] | None = None,
) -> CapabilityMatrix:
    """Best-effort feature detection against Platform API."""
    base = api_endpoint(endpoint)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    fetcher = get or _get

    gateway_ok = await _ok(fetcher, f"{base}/genai/llmgw/v1/models", headers)

    workload_status, _ = await fetcher(f"{base}/workloads/?limit=1", headers)
    workload_ok = workload_status in {200, 201, 403}

    memory_status, _ = await fetcher(f"{base}/genai/agenticMemory/spaces/?limit=1", headers)
    memory_ok = memory_status in {200, 201, 403}

    account_status, account = await fetcher(f"{base}/account/info/", headers)
    code_to_workload = False
    if account_status == 200 and isinstance(account, dict):
        flags = account.get("featureFlags") or account.get("flags") or {}
        if isinstance(flags, dict):
            code_to_workload = bool(
                flags.get("ENABLE_WORKLOAD_API_CONTAINERS")
                or flags.get("enableWorkloadApiContainers")
            )

    return CapabilityMatrix(
        llm_gateway=gateway_ok,
        agent_app=shutil.which("dr") is not None,
        workload=workload_ok,
        memory=memory_ok,
        code_to_workload=code_to_workload,
    )


async def _ok(
    fetcher: Callable[[str, dict[str, str]], Awaitable[tuple[int, object]]],
    url: str,
    headers: dict[str, str],
) -> bool:
    status, _ = await fetcher(url, headers)
    return 200 <= status < 300 or status == 403


async def _get(url: str, headers: dict[str, str]) -> tuple[int, object]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, headers=headers)
        try:
            body: object = response.json()
        except ValueError:
            body = {"detail": response.text}
        return response.status_code, body
