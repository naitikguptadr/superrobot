"""Platform rules unit tests."""

import pytest

from superrobot.dr.platform_rules import (
    PyprojectRemovalError,
    validate_flat_imports,
    validate_pyproject,
    validate_runtime_params,
)
from superrobot.models.agent_config import AgentConfig


def test_validate_flat_imports_happy_path() -> None:
    code = "from planner import PlannerAgent"
    assert validate_flat_imports(code) == []


def test_validate_flat_imports_violation() -> None:
    code = "from agent.agent.planner import PlannerAgent"
    violations = validate_flat_imports(code)
    assert len(violations) == 1
    assert "DRUM" in violations[0]


def test_validate_pyproject_additive_only() -> None:
    original = '[project]\ndependencies = ["requests", "httpx"]\n'
    generated = '[project]\ndependencies = ["requests", "httpx", "datarobot"]\n'
    validate_pyproject(original, generated)


def test_validate_pyproject_removal_raises() -> None:
    original = '[project]\ndependencies = ["requests", "httpx"]\n'
    generated = '[project]\ndependencies = ["requests"]\n'
    with pytest.raises(PyprojectRemovalError):
        validate_pyproject(original, generated)


def test_validate_runtime_params_all_present() -> None:
    config = AgentConfig(runtime_param_keys=["OPENAI_API_KEY"])
    custom = '_RUNTIME_PARAM_KEYS = ["OPENAI_API_KEY"]'
    infra = "OPENAI_API_KEY"
    env = "OPENAI_API_KEY="
    assert validate_runtime_params(config, custom, infra, env) == []
