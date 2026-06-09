"""Encoded DR platform gotchas as validator functions."""

from __future__ import annotations

import re

from superrobot.models.agent_config import AgentConfig

NESTED_IMPORT_PATTERN = re.compile(r"from\s+agent\.agent\.")


class PyprojectRemovalError(Exception):
    """Raised when generated pyproject.toml removes packages from original."""


class PlatformRuleViolation(Exception):
    """Raised when generated code violates a DR platform rule."""


def validate_flat_imports(content: str) -> list[str]:
    """Return violations for non-flat DRUM imports."""
    violations: list[str] = []
    for i, line in enumerate(content.splitlines(), 1):
        if NESTED_IMPORT_PATTERN.search(line):
            msg = f"Line {i}: nested import violates DRUM flat bundle rule: {line.strip()}"
            violations.append(msg)
    return violations


def validate_pyproject(original: str, generated: str) -> None:
    """Ensure generated pyproject.toml is additive-only."""
    orig_deps = _extract_dependencies(original)
    gen_deps = _extract_dependencies(generated)
    removed = orig_deps - gen_deps
    if removed:
        raise PyprojectRemovalError(f"Generated pyproject.toml removed packages: {sorted(removed)}")


def validate_runtime_params(
    config: AgentConfig,
    custom_py: str,
    infra_py: str,
    env_template: str,
) -> list[str]:
    """Cross-check runtime params appear in all three required locations."""
    violations: list[str] = []
    for key in config.runtime_param_keys:
        if key not in custom_py:
            violations.append(f"{key} missing from custom.py _RUNTIME_PARAM_KEYS")
        if key not in infra_py:
            violations.append(f"{key} missing from infra/infra/agent.py")
        if key not in env_template:
            violations.append(f"{key} missing from .env.template")
    return violations


def validate_endpoint_usage(content: str) -> list[str]:
    """Warn if prediction API URL might be confused with platform API."""
    violations: list[str] = []
    if "DATAROBOT_PREDICTION_API_URL" in content and "dr.Client()" in content:
        violations.append(
            "DATAROBOT_PREDICTION_API_URL used with dr.Client() — use DATAROBOT_ENDPOINT"
        )
    return violations


def _extract_dependencies(pyproject_content: str) -> set[str]:
    """Extract dependency names from pyproject.toml content."""
    match = re.search(r"dependencies\s*=\s*\[(.*?)\]", pyproject_content, re.DOTALL)
    if not match:
        return set()
    block = match.group(1)
    packages = re.findall(r'["\']([^"\']+)["\']', block)
    return {pkg.split(">=")[0].split("==")[0].strip() for pkg in packages}
