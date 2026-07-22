"""Setup package exports."""

from superrobot.setup.doctor import run_doctor
from superrobot.setup.endpoints import api_endpoint, normalize_endpoint
from superrobot.setup.models import CapabilityMatrix, DoctorResult, SetupState
from superrobot.setup.runner import run_setup

__all__ = [
    "CapabilityMatrix",
    "DoctorResult",
    "SetupState",
    "api_endpoint",
    "normalize_endpoint",
    "run_doctor",
    "run_setup",
]
