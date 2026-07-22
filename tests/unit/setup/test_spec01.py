"""Spec 01 — endpoint, config, gateway, doctor tests."""

from __future__ import annotations

import asyncio
import stat
from pathlib import Path

import pytest

from superrobot.setup.config import load_env_file, load_state, save_state, write_token_env
from superrobot.setup.doctor import run_doctor
from superrobot.setup.endpoints import (
    EndpointError,
    api_endpoint,
    gateway_base_url,
    normalize_endpoint,
)
from superrobot.setup.gateway import GatewayError, verify_gateway
from superrobot.setup.models import AuthMethod, CapabilityMatrix, SetupState
from superrobot.setup.probes import probe_capabilities


def test_normalize_strips_api_v2_and_slash() -> None:
    assert normalize_endpoint("https://app.datarobot.com/api/v2/") == "https://app.datarobot.com"
    assert api_endpoint("https://app.datarobot.com/api/v2") == "https://app.datarobot.com/api/v2"


def test_normalize_rejects_prediction_urls() -> None:
    with pytest.raises(EndpointError, match="Prediction"):
        normalize_endpoint("https://prediction.datarobot.com")


def test_gateway_base_url() -> None:
    assert (
        gateway_base_url("https://app.datarobot.com")
        == "https://app.datarobot.com/api/v2/genai/llmgw"
    )


def test_state_roundtrip_without_secrets(tmp_path: Path) -> None:
    state = SetupState(
        endpoint="https://app.datarobot.com",
        auth_method=AuthMethod.API_TOKEN,
        capabilities=CapabilityMatrix(llm_gateway=True, workload=True),
        model="azure/gpt-test",
    )
    save_state(state, tmp_path)
    restored = load_state(tmp_path)
    assert restored == state
    raw = (tmp_path / "setup.json").read_text()
    assert "DATAROBOT_API_TOKEN" not in raw
    assert "secret" not in raw


def test_token_env_is_owner_only(tmp_path: Path) -> None:
    path = write_token_env(
        endpoint="https://app.datarobot.com/api/v2",
        token="secret-token",
        model="azure/gpt-test",
        root=tmp_path,
    )
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    env = load_env_file(tmp_path)
    assert env["DATAROBOT_API_TOKEN"] == "secret-token"


def test_verify_gateway_success_and_redacts_token() -> None:
    async def transport(
        method: str, url: str, headers: dict[str, str], payload: object | None
    ) -> tuple[int, object]:
        assert "Bearer secret-token" in headers["Authorization"]
        if method == "GET":
            return 401, {"detail": "Token secret-token rejected"}
        return 200, {"ok": True}

    async def run() -> None:
        with pytest.raises(GatewayError, match=r"\*\*\*") as exc:
            await verify_gateway(
                "https://app.datarobot.com",
                "secret-token",
                transport=transport,
            )
        assert "secret-token" not in str(exc.value)

        async def ok_transport(
            method: str, url: str, headers: dict[str, str], payload: object | None
        ) -> tuple[int, object]:
            return 200, {"data": []}

        assert await verify_gateway(
            "https://app.datarobot.com", "secret-token", transport=ok_transport
        )

    asyncio.run(run())


def test_probe_capabilities_marks_workload_and_memory() -> None:
    async def get(url: str, headers: dict[str, str]) -> tuple[int, object]:
        if "workloads" in url:
            return 200, {"data": []}
        if "agenticMemory" in url or "Memory" in url:
            return 403, {"detail": "forbidden"}
        if "account/info" in url:
            return 200, {"featureFlags": {"ENABLE_WORKLOAD_API_CONTAINERS": True}}
        if "llmgw" in url:
            return 200, {"data": []}
        return 404, {}

    async def run() -> None:
        caps = await probe_capabilities("https://app.datarobot.com", "token", get=get)
        assert caps.workload is True
        assert caps.memory is True
        assert caps.code_to_workload is True

    asyncio.run(run())


def test_doctor_ready_when_endpoint_auth_gateway_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_token_env(
        endpoint="https://app.datarobot.com/api/v2",
        token="tok",
        model="azure/gpt-test",
        root=tmp_path,
    )
    save_state(
        SetupState(
            endpoint="https://app.datarobot.com",
            auth_method=AuthMethod.API_TOKEN,
            capabilities=CapabilityMatrix(llm_gateway=True),
        ),
        tmp_path,
    )

    async def fake_verify(endpoint: str, token: str, **kwargs: object) -> bool:
        return True

    async def fake_caps(endpoint: str, token: str, **kwargs: object) -> CapabilityMatrix:
        return CapabilityMatrix(llm_gateway=True, workload=True, memory=False)

    async def fake_dr() -> object:
        from superrobot.setup.probes import AuthProbe

        return AuthProbe(AuthMethod.NONE, False, "skip")

    monkeypatch.setattr("superrobot.setup.doctor.verify_gateway", fake_verify)
    monkeypatch.setattr("superrobot.setup.doctor.probe_capabilities", fake_caps)
    monkeypatch.setattr("superrobot.setup.doctor.check_dr_auth", fake_dr)

    result = asyncio.run(run_doctor(config_root=str(tmp_path)))
    assert result.ready is True
    assert result.state is not None
    assert result.state.capabilities.workload is True
