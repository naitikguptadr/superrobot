"""Deploy subprocess wrapper — Stage 6."""

from __future__ import annotations

from dataclasses import dataclass

from superrobot.dr.cli_wrapper import DrCliWrapper, DrCommandResult

DEPLOY_WARNINGS = [
    "Deploy may take 15-20 min even for Python-only changes (BUZZOK-30076).",
    "If deploy fails, Pulumi deletes deployment logs. Consider manual UI deploy to preserve logs.",
    "Frontend rebuild runs on every deploy if UI components are present (BUZZOK-30076).",
]


@dataclass
class DeployResult:
    """Result of a deploy attempt."""

    success: bool
    stdout: str
    stderr: str
    error_message: str | None = None
    warnings: list[str] | None = None


async def deploy(
    cwd: str | None = None,
    has_ui: bool = False,
    cli: DrCliWrapper | None = None,
) -> DeployResult:
    """Run dr task run deploy and parse result."""
    wrapper = cli or DrCliWrapper()
    warnings = list(DEPLOY_WARNINGS)
    if not has_ui:
        warnings = [w for w in warnings if "Frontend" not in w]

    result = await wrapper.task_run_deploy(cwd=cwd)
    error = _parse_error(result) if not result.ok else None

    return DeployResult(
        success=result.ok,
        stdout=result.stdout,
        stderr=result.stderr,
        error_message=error,
        warnings=warnings,
    )


def _parse_error(result: DrCommandResult) -> str:
    if result.stderr.strip():
        lines = result.stderr.strip().splitlines()
        return lines[-1]
    if result.stdout.strip():
        lines = result.stdout.strip().splitlines()
        return lines[-1]
    return f"Deploy failed with exit code {result.returncode}"
