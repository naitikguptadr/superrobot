"""Setup, auth, endpoint, and capability contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum


class AuthMethod(StrEnum):
    DR_CLI = "dr-cli"
    API_TOKEN = "api-token"
    NONE = "none"


@dataclass(frozen=True)
class CapabilityMatrix:
    """Platform features discovered during setup/doctor."""

    llm_gateway: bool = False
    agent_app: bool = False
    workload: bool = False
    memory: bool = False
    code_to_workload: bool = False

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True)
class SetupState:
    """Non-secret setup snapshot persisted for doctor and the shell."""

    endpoint: str
    auth_method: AuthMethod
    capabilities: CapabilityMatrix = field(default_factory=CapabilityMatrix)
    model: str = "azure/gpt-5-5-2026-04-23"

    def to_dict(self) -> dict[str, object]:
        return {
            "endpoint": self.endpoint,
            "auth_method": self.auth_method.value,
            "capabilities": self.capabilities.to_dict(),
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> SetupState:
        caps_raw = value.get("capabilities")
        caps = CapabilityMatrix()
        if isinstance(caps_raw, dict):
            caps = CapabilityMatrix(
                llm_gateway=bool(caps_raw.get("llm_gateway", False)),
                agent_app=bool(caps_raw.get("agent_app", False)),
                workload=bool(caps_raw.get("workload", False)),
                memory=bool(caps_raw.get("memory", False)),
                code_to_workload=bool(caps_raw.get("code_to_workload", False)),
            )
        return cls(
            endpoint=str(value["endpoint"]),
            auth_method=AuthMethod(str(value["auth_method"])),
            capabilities=caps,
            model=str(value.get("model", "azure/gpt-5-5-2026-04-23")),
        )


@dataclass(frozen=True)
class DoctorResult:
    """Aggregate readiness for CLI exit codes and UI."""

    ready: bool
    checks: tuple[tuple[str, bool, str], ...]
    state: SetupState | None = None
