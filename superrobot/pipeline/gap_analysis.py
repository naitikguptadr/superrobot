"""Gap Analysis — re-run platform-rule checks against a generated package on disk.

Reuses the same validators `config_generator.py` already runs at generation time
(`dr/platform_rules.py`), classified into blocking vs. warning severity so `deploy`
can enforce: "Gap Analysis findings that are blocking must stop deploy. Warnings
need explicit waiver." (shell/prompts/system.md)
"""

from __future__ import annotations

from pathlib import Path

from superrobot.dr.platform_rules import (
    PyprojectRemovalError,
    validate_endpoint_usage,
    validate_flat_imports,
    validate_pyproject,
)
from superrobot.models.gap_result import GapFinding, GapReport

_FIXED_ENV_KEYS = {"PROMPT_TEMPLATE_ID", "DATAROBOT_ENDPOINT"}


def run_gap_analysis(package_dir: str | Path, source_repo: str | Path | None = None) -> GapReport:
    """Run Gap Analysis against a SuperRobot-generated package directory."""
    root = Path(package_dir)
    custom_py = _read(root / "agent" / "agent" / "custom.py")
    infra_py = _read(root / "infra" / "infra" / "agent.py")
    env_template = _read(root / ".env.template")
    pyproject = _read(root / "pyproject.toml")

    findings: list[GapFinding] = []

    if custom_py is None and env_template is None and pyproject is None:
        findings.append(
            GapFinding(
                rule="not-a-package",
                severity="blocking",
                message=f"No SuperRobot generated package found at {root}",
            )
        )
        return GapReport(findings=findings)

    agent_dir = root / "agent" / "agent"
    for py_file in sorted(agent_dir.rglob("*.py")) if agent_dir.is_dir() else []:
        content = py_file.read_text()
        rel = str(py_file.relative_to(root))
        for violation in validate_flat_imports(content):
            findings.append(
                GapFinding(rule="flat-imports", severity="blocking", message=violation, file=rel)
            )
        for violation in validate_endpoint_usage(content):
            findings.append(
                GapFinding(rule="endpoint-usage", severity="warning", message=violation, file=rel)
            )

    if source_repo is not None and pyproject is not None:
        original_path = Path(source_repo) / "pyproject.toml"
        if original_path.is_file():
            try:
                validate_pyproject(original_path.read_text(), pyproject)
            except PyprojectRemovalError as exc:
                findings.append(
                    GapFinding(
                        rule="pyproject-removal",
                        severity="blocking",
                        message=str(exc),
                        file="pyproject.toml",
                    )
                )

    if env_template is not None and custom_py is not None and infra_py is not None:
        for key in _parse_runtime_keys(env_template):
            if key not in custom_py:
                findings.append(
                    GapFinding(
                        rule="runtime-param",
                        severity="warning",
                        message=f"{key} missing from _RUNTIME_PARAM_KEYS",
                        file="agent/agent/custom.py",
                    )
                )
            if key not in infra_py:
                findings.append(
                    GapFinding(
                        rule="runtime-param",
                        severity="warning",
                        message=f"{key} missing from infra/infra/agent.py",
                        file="infra/infra/agent.py",
                    )
                )

    return GapReport(findings=findings)


def _parse_runtime_keys(env_template: str) -> list[str]:
    keys: list[str] = []
    for line in env_template.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", maxsplit=1)[0].strip()
        if key and key not in _FIXED_ENV_KEYS:
            keys.append(key)
    return keys


def _read(path: Path) -> str | None:
    return path.read_text() if path.is_file() else None
