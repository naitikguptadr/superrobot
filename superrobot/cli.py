"""SuperRobot CLI — DataRobot-native control plane."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.table import Table

from superrobot import __version__
from superrobot.setup.doctor import run_doctor
from superrobot.setup.runner import run_setup
from superrobot.shell_launcher import find_shell_entry

if TYPE_CHECKING:
    from superrobot.models.gap_result import GapReport
    from superrobot.models.receipt import Receipt
    from superrobot.setup.models import SetupState

console = Console()
app = typer.Typer(
    name="superrobot",
    help="Bring any Python agent to DataRobot — migrate, validate, deploy, operate.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Args that should still go through Typer's own parsing/dispatch rather than
# being handed off to the interactive shell.
_KNOWN_SUBCOMMANDS = {
    "doctor",
    "setup",
    "status",
    "scan",
    "analyze",
    "generate",
    "transform",
    "validate",
    "deploy",
    "memory",
    "receipt",
}
_KNOWN_GLOBAL_FLAGS = {
    "--help",
    "-h",
    "--version",
    "-V",
    "--install-completion",
    "--show-completion",
}


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"superrobot {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", "-V", callback=_version_callback, is_eager=True),
    ] = None,
) -> None:
    """SuperRobot — DataRobot-native brownfield control plane."""


def should_launch_shell(argv: list[str]) -> bool:
    """True when argv doesn't look like a real subcommand invocation, so
    `superrobot` (bare, or with shell-only flags like --print) should launch
    the interactive shell instead of falling through to Typer's own parsing
    (which would otherwise error on an unrecognized command/option).

    Tradeoff: this only inspects argv[0], so a free-text prompt that happens
    to start with a subcommand word (e.g. "generate a poem about pandas")
    dispatches to Typer, not the shell, and Typer will report a usage error
    for that subcommand rather than treating it as a prompt. Accepted as a
    known limitation rather than a heuristic that guesses at intent."""
    if not argv:
        return True
    return argv[0] not in _KNOWN_SUBCOMMANDS and argv[0] not in _KNOWN_GLOBAL_FLAGS


def launch_shell(argv: list[str]) -> None:
    """Replace the current process with the built Pi shell, passing argv through.

    Called from main_entry(), before Typer/Click ever runs -- typer.Exit is
    only translated to a clean process exit inside Click's own dispatch, so
    failures here must raise SystemExit directly instead.
    """
    shell_entry = find_shell_entry()
    if shell_entry is None:
        console.print(
            "[red]Interactive shell not found[/] — build it first:\n"
            "  [cyan]cd shell && npm install && npm run build[/]\n"
            "or set [cyan]SUPERROBOT_SHELL_DIR[/] to a directory containing dist/cli.js.\n\n"
            "Run [cyan]superrobot --help[/] to see available subcommands instead."
        )
        raise SystemExit(1)

    node = shutil.which("node")
    if node is None:
        console.print("[red]node not found on PATH[/] — required to run the interactive shell.")
        raise SystemExit(1)

    try:
        os.execvp(node, [node, str(shell_entry), *argv])
    except OSError as exc:
        console.print(f"[red]Failed to launch the interactive shell:[/] {exc}")
        raise SystemExit(1) from exc


def main_entry() -> None:
    """Console-script entry point: launch the shell for bare/unrecognized
    invocations, otherwise dispatch to Typer as usual."""
    import sys

    argv = sys.argv[1:]
    if should_launch_shell(argv):
        launch_shell(argv)
        return
    app()


@app.command("doctor")
def doctor_cmd(
    json_out: Annotated[bool, typer.Option("--json")] = False,
    skip_gateway: Annotated[bool, typer.Option("--skip-gateway")] = False,
    config_dir: Annotated[Path | None, typer.Option("--config-dir")] = None,
) -> None:
    """Health check — endpoint, auth, LLM Gateway, capabilities."""
    result = asyncio.run(
        run_doctor(
            config_root=str(config_dir) if config_dir else None,
            skip_gateway=skip_gateway,
        )
    )
    if json_out:
        console.print_json(
            json.dumps(
                {
                    "ready": result.ready,
                    "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in result.checks],
                    "state": result.state.to_dict() if result.state else None,
                }
            )
        )
    else:
        table = Table(title="SuperRobot doctor")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Detail")
        for name, ok, detail in result.checks:
            table.add_row(name, "[green]ok[/]" if ok else "[red]fail[/]", detail)
        console.print(table)
        console.print(
            "[green]● ready[/]" if result.ready else "[yellow]● not ready — run superrobot setup[/]"
        )
    raise typer.Exit(0 if result.ready else 1)


@app.command("setup")
def setup_cmd(
    endpoint: Annotated[str | None, typer.Option("--endpoint")] = None,
    token: Annotated[str | None, typer.Option("--token", envvar="DATAROBOT_API_TOKEN")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    skip_gateway: Annotated[bool, typer.Option("--skip-gateway")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Non-interactive")] = False,
    config_dir: Annotated[Path | None, typer.Option("--config-dir")] = None,
) -> None:
    """First-run wizard — DataRobot endpoint, auth, Gateway verify, capability probe."""
    result = asyncio.run(
        run_setup(
            console=console,
            config_root=config_dir,
            endpoint=endpoint,
            token=token,
            model=model,
            skip_gateway=skip_gateway,
            non_interactive=yes,
        )
    )
    raise typer.Exit(0 if result.ready else 1)


@app.command("status")
def status_cmd(
    config_dir: Annotated[Path | None, typer.Option("--config-dir")] = None,
) -> None:
    """One-line readiness."""
    result = asyncio.run(
        run_doctor(
            config_root=str(config_dir) if config_dir else None,
            skip_gateway=True,
        )
    )
    if result.ready:
        console.print("[green]●[/] SuperRobot ready")
        raise typer.Exit(0)
    console.print("[yellow]●[/] Setup incomplete — run [cyan]superrobot setup[/]")
    raise typer.Exit(1)


@app.command("scan")
def scan_cmd(
    source: Annotated[str, typer.Argument(help="Local path or GitHub URL")],
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Stage 1 — static scan; emit ScanResult."""
    from superrobot.engine.pipeline import TransformEngine

    engine = TransformEngine()
    repo = asyncio.run(engine.resolve_source(source))
    result = engine.run_scan(repo)
    if json_out:
        console.print_json(result.model_dump_json())
    else:
        console.print(
            f"[cyan]framework[/]={result.detected_framework} "
            f"[cyan]confidence[/]={result.confidence:.0%} "
            f"[cyan]entries[/]={len(result.entry_points)}"
        )
    raise typer.Exit(0)


@app.command("analyze")
def analyze_cmd(
    source: Annotated[str, typer.Argument(help="Local path or GitHub URL")],
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Stages 1–2 — scan + analyze; emit AnalysisResult."""
    from superrobot.engine.pipeline import TransformEngine

    engine = TransformEngine()

    async def _run() -> None:
        repo = await engine.resolve_source(source)
        scan = engine.run_scan(repo)
        analysis = await engine.run_analyze(scan)
        if json_out:
            console.print_json(analysis.model_dump_json())
        else:
            console.print(
                f"[cyan]purpose[/]={analysis.agent_purpose}\n"
                f"[cyan]framework[/]={analysis.dr_framework.value} "
                f"[cyan]confidence[/]={analysis.confidence:.0%}"
            )

    asyncio.run(_run())
    raise typer.Exit(0)


@app.command("generate")
def generate_cmd(
    source: Annotated[str, typer.Argument(help="Local path or GitHub URL")],
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")],
    framework: Annotated[str | None, typer.Option("--framework")] = None,
) -> None:
    """Stages 1–3 — write Agent App packaging into --output-dir."""
    from superrobot.engine.pipeline import TransformEngine

    engine = TransformEngine()

    async def _run() -> None:
        ctx = await engine.transform(
            source,
            output_dir=output_dir,
            skip_eval=True,
            skip_deploy=True,
            framework=framework,
        )
        console.print(f"[green]wrote[/] {len(ctx.files)} files → {ctx.output_dir}")

    asyncio.run(_run())
    raise typer.Exit(0)


@app.command("transform")
def transform_cmd(
    source: Annotated[str, typer.Argument(help="Local path or GitHub URL")],
    output_dir: Annotated[Path | None, typer.Option("--output-dir", "-o")] = None,
    skip_eval: Annotated[bool, typer.Option("--skip-eval")] = False,
    framework: Annotated[str | None, typer.Option("--framework")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Full brownfield transform (Scan → Analyze → Generate → Eval)."""
    from superrobot.engine.pipeline import TransformEngine

    engine = TransformEngine()

    async def _run() -> None:
        ctx = await engine.transform(
            source,
            output_dir=output_dir,
            skip_eval=skip_eval,
            skip_deploy=True,
            framework=framework,
        )
        payload = {
            "repo_path": ctx.repo_path,
            "output_dir": str(ctx.output_dir),
            "scan": ctx.scan.model_dump() if ctx.scan else None,
            "analysis": ctx.analysis.model_dump() if ctx.analysis else None,
            "files": sorted(ctx.files.keys()),
            "eval": ctx.eval_summary.model_dump() if ctx.eval_summary else None,
        }
        if json_out:
            console.print_json(json.dumps(payload, default=str))
        else:
            console.print(f"[green]transform complete[/] files={len(ctx.files)} → {ctx.output_dir}")

    asyncio.run(_run())
    raise typer.Exit(0)


@app.command("validate")
def validate_cmd(
    path: Annotated[Path, typer.Argument(help="Generated package directory")],
    source: Annotated[
        Path | None,
        typer.Option("--source", help="Original repo — enables the pyproject-removal check"),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Gap Analysis — platform-rule findings, blocking vs. warning."""
    from superrobot.pipeline.gap_analysis import run_gap_analysis

    if not path.is_dir():
        console.print(f"[red]Not a directory[/] {path}")
        raise typer.Exit(2)

    report = run_gap_analysis(path, source_repo=source)
    if json_out:
        console.print_json(report.model_dump_json())
    else:
        _print_gap_findings(report)
    raise typer.Exit(1 if report.blocking else 0)


def _print_gap_findings(report: GapReport) -> None:
    if not report.findings:
        console.print("[green]no gaps found[/]")
        return
    for finding in report.findings:
        color = "red" if finding.severity == "blocking" else "yellow"
        location = f" ({finding.file})" if finding.file else ""
        console.print(f"[{color}]{finding.severity}[/] {finding.rule}: {finding.message}{location}")
    if report.blocking:
        console.print(
            f"[red]{len(report.blocking)} blocking finding(s)[/] — deploy refuses without --waive"
        )


@app.command("deploy")
def deploy_cmd(
    path: Annotated[Path, typer.Argument(help="Generated package directory")],
    target: Annotated[
        str,
        typer.Option("--target", help="Deploy target: agent-app or workload"),
    ] = "agent-app",
    has_ui: Annotated[bool, typer.Option("--has-ui")] = False,
    image_uri: Annotated[
        str | None, typer.Option("--image-uri", help="Built container image (workload target)")
    ] = None,
    secret: Annotated[
        list[str] | None,
        typer.Option("--secret", help="KEY=credential:<id> (workload target, repeatable)"),
    ] = None,
    waive: Annotated[
        bool, typer.Option("--waive", help="Proceed despite blocking Gap Analysis findings")
    ] = False,
    config_dir: Annotated[Path | None, typer.Option("--config-dir")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Deploy generated packaging to DataRobot Agent App or the Workload API."""
    if target not in {"agent-app", "workload"}:
        console.print(
            f"[red]Unsupported target[/] {target!r} — use [cyan]agent-app[/] or [cyan]workload[/]"
        )
        raise typer.Exit(2)
    if not path.is_dir():
        console.print(f"[red]Not a directory[/] {path}")
        raise typer.Exit(2)

    if target == "agent-app":
        raise typer.Exit(
            asyncio.run(
                _deploy_agent_app(
                    path, has_ui=has_ui, waive=waive, config_dir=config_dir, json_out=json_out
                )
            )
        )
    raise typer.Exit(
        asyncio.run(
            _deploy_workload(
                path,
                image_uri=image_uri,
                secrets=secret,
                waive=waive,
                config_dir=config_dir,
                json_out=json_out,
            )
        )
    )


def _resolve_credentials(config_dir: Path | None) -> tuple[str, str, SetupState | None]:
    """Endpoint, token, and persisted SetupState — same resolution order as doctor."""
    from superrobot.setup.config import load_env_file, load_state

    env = load_env_file(config_dir)
    state = load_state(config_dir)
    endpoint = (
        env.get("DATAROBOT_ENDPOINT")
        or (state.endpoint if state else "")
        or os.environ.get("DATAROBOT_ENDPOINT", "")
    )
    token = env.get("DATAROBOT_API_TOKEN") or os.environ.get("DATAROBOT_API_TOKEN", "")
    return endpoint, token, state


def _resolve_model(config_dir: Path | None) -> str:
    from superrobot.dr.llm_gateway import DEFAULT_MODEL

    _, _, state = _resolve_credentials(config_dir)
    return (state.model if state else "") or os.environ.get("SUPERROBOT_MODEL", DEFAULT_MODEL)


def _record_receipt(
    *,
    target: str,
    action: str,
    success: bool,
    config_dir: Path | None,
    manifest_dir: Path,
    gap_report: GapReport | None = None,
    waived: bool = False,
    error_message: str | None = None,
    image_uri: str | None = None,
    has_ui: bool = False,
    replaces: str | None = None,
) -> None:
    import uuid
    from datetime import UTC, datetime

    from superrobot.models.receipt import Receipt
    from superrobot.pipeline.receipts import save_receipt

    gap_summary = {
        "blocking": len(gap_report.blocking) if gap_report else 0,
        "warnings": len(gap_report.warnings) if gap_report else 0,
    }
    waived_findings = (
        [f.message for f in gap_report.blocking]
        if gap_report and waived and gap_report.blocking
        else []
    )
    receipt = Receipt(
        id=uuid.uuid4().hex[:12],
        created_at=datetime.now(UTC).isoformat(),
        target=target,  # type: ignore[arg-type]
        action=action,  # type: ignore[arg-type]
        success=success,
        model=_resolve_model(config_dir),
        gap_summary=gap_summary,
        waived_findings=waived_findings,
        error_message=error_message,
        replaces=replaces,
        manifest_dir=str(manifest_dir),
        image_uri=image_uri,
        has_ui=has_ui,
    )
    save_receipt(receipt, config_dir)


def _gap_gate(
    path: Path,
    *,
    waive: bool,
    json_out: bool,
    target: str,
    config_dir: Path | None,
    image_uri: str | None = None,
    has_ui: bool = False,
    replaces: str | None = None,
) -> GapReport | None:
    """Run Gap Analysis; print findings; record+return None if deploy is blocked."""
    from superrobot.pipeline.gap_analysis import run_gap_analysis

    report = run_gap_analysis(path)
    if report.blocking and not waive:
        if json_out:
            console.print_json(
                json.dumps(
                    {
                        "success": False,
                        "target": target,
                        "blocked_by_gap_analysis": True,
                        "findings": [f.model_dump() for f in report.blocking],
                    }
                )
            )
        else:
            _print_gap_findings(report)
            console.print("[red]Deploy refused[/] — fix blocking findings or pass --waive")
        _record_receipt(
            target=target,
            action="blocked",
            success=False,
            config_dir=config_dir,
            manifest_dir=path,
            gap_report=report,
            error_message="; ".join(f.message for f in report.blocking),
            image_uri=image_uri,
            has_ui=has_ui,
            replaces=replaces,
        )
        return None
    if report.findings and not json_out:
        _print_gap_findings(report)
    return report


async def _deploy_agent_app(
    path: Path,
    *,
    has_ui: bool,
    waive: bool,
    config_dir: Path | None,
    json_out: bool,
    replaces: str | None = None,
) -> int:
    from superrobot.pipeline.deployer import DEPLOY_WARNINGS, deploy

    gap_report = _gap_gate(
        path,
        waive=waive,
        json_out=json_out,
        target="agent-app",
        config_dir=config_dir,
        has_ui=has_ui,
        replaces=replaces,
    )
    if gap_report is None:
        return 1

    for warning in DEPLOY_WARNINGS:
        if not has_ui and "Frontend" in warning:
            continue
        console.print(f"[yellow]![/] {warning}")

    result = await deploy(cwd=str(path), has_ui=has_ui)
    payload = {
        "success": result.success,
        "target": "agent-app",
        "warnings": result.warnings,
        "error_message": result.error_message,
    }
    if json_out:
        console.print_json(json.dumps(payload))
    elif result.success:
        console.print("[green]deploy succeeded[/]")
    else:
        console.print(f"[red]deploy failed[/] {result.error_message or ''}")
    _record_receipt(
        target="agent-app",
        action="deployed" if result.success else "failed",
        success=result.success,
        config_dir=config_dir,
        manifest_dir=path,
        gap_report=gap_report,
        waived=bool(gap_report.blocking),
        error_message=result.error_message,
        has_ui=has_ui,
        replaces=replaces,
    )
    return 0 if result.success else 1


async def _deploy_workload(
    path: Path,
    *,
    image_uri: str | None,
    secrets: list[str] | None,
    waive: bool,
    config_dir: Path | None,
    json_out: bool,
    replaces: str | None = None,
) -> int:
    from superrobot.pipeline.workload_deployer import deploy_workload

    if not image_uri:
        console.print("[red]--image-uri is required for --target workload[/]")
        return 2

    secret_map: dict[str, str] = {}
    for item in secrets or []:
        if "=" not in item:
            console.print(f"[red]Invalid --secret[/] {item!r} — expected KEY=VALUE")
            return 2
        key, value = item.split("=", maxsplit=1)
        secret_map[key] = value

    endpoint, token, state = _resolve_credentials(config_dir)
    if not endpoint or not token:
        console.print("[red]Not authenticated[/] — run [cyan]superrobot setup[/]")
        return 1
    if not state or not state.capabilities.workload:
        console.print(
            "[red]Workload API not entitled on this account[/] — "
            "run [cyan]superrobot doctor[/] to re-probe capabilities"
        )
        return 1

    gap_report = _gap_gate(
        path,
        waive=waive,
        json_out=json_out,
        target="workload",
        config_dir=config_dir,
        image_uri=image_uri,
        replaces=replaces,
    )
    if gap_report is None:
        return 1

    result = await deploy_workload(
        manifest_dir=str(path),
        image_uri=image_uri,
        endpoint=endpoint,
        token=token,
        secrets=secret_map,
    )
    payload = {
        "success": result.success,
        "target": "workload",
        "action": result.action,
        "workload_id": result.workload_id,
        "error_message": result.error_message,
    }
    if json_out:
        console.print_json(json.dumps(payload))
    elif result.success:
        console.print(f"[green]workload {result.action}[/] id={result.workload_id}")
    else:
        console.print(f"[red]workload deploy failed[/] {result.error_message or ''}")
    _record_receipt(
        target="workload",
        action=result.action or "failed",
        success=result.success,
        config_dir=config_dir,
        manifest_dir=path,
        gap_report=gap_report,
        waived=bool(gap_report.blocking),
        error_message=result.error_message,
        image_uri=image_uri,
        replaces=replaces,
    )
    return 0 if result.success else 1


memory_app = typer.Typer(help="Memory API space provisioning.")
app.add_typer(memory_app, name="memory")


@memory_app.command("ensure")
def memory_ensure_cmd(
    name: Annotated[str, typer.Argument(help="Memory space name")],
    config_dir: Annotated[Path | None, typer.Option("--config-dir")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Get-or-create a named Memory API space."""
    from superrobot.pipeline.memory_provisioner import ensure_space

    endpoint, token, state = _resolve_credentials(config_dir)
    if not endpoint or not token:
        console.print("[red]Not authenticated[/] — run [cyan]superrobot setup[/]")
        raise typer.Exit(1)
    if not state or not state.capabilities.memory:
        console.print(
            "[red]Memory API not entitled on this account[/] — "
            "run [cyan]superrobot doctor[/] to re-probe capabilities"
        )
        raise typer.Exit(1)

    result = asyncio.run(ensure_space(name, endpoint=endpoint, token=token))
    payload = {
        "success": result.success,
        "action": result.action,
        "space_id": result.space_id,
        "error_message": result.error_message,
    }
    if json_out:
        console.print_json(json.dumps(payload))
    elif result.success:
        console.print(f"[green]memory space {result.action}[/] id={result.space_id}")
    else:
        console.print(f"[red]memory ensure failed[/] {result.error_message or ''}")
    raise typer.Exit(0 if result.success else 1)


receipt_app = typer.Typer(help="Deploy receipts — attribution, history, diagnostics.")
app.add_typer(receipt_app, name="receipt")


def _print_receipt(receipt: Receipt) -> None:
    status = "[green]success[/]" if receipt.success else "[red]failed[/]"
    console.print(
        f"[cyan]{receipt.id}[/] {receipt.target}/{receipt.action} {status} "
        f"model={receipt.model} at={receipt.created_at}"
    )
    if receipt.replaces:
        console.print(f"  replaces: {receipt.replaces}")
    if receipt.waived_findings:
        console.print(f"  waived: {len(receipt.waived_findings)} blocking finding(s)")
    if receipt.error_message:
        console.print(f"  error: {receipt.error_message}")


@receipt_app.command("show")
def receipt_show_cmd(
    receipt_id: Annotated[str | None, typer.Argument(help="Receipt id (default: latest)")] = None,
    config_dir: Annotated[Path | None, typer.Option("--config-dir")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show one receipt — defaults to the most recent."""
    from superrobot.pipeline.receipts import latest_receipt, load_receipt

    receipt = load_receipt(receipt_id, config_dir) if receipt_id else latest_receipt(config_dir)
    if receipt is None:
        console.print("[yellow]No receipts found[/]")
        raise typer.Exit(1)
    if json_out:
        console.print_json(receipt.model_dump_json())
    else:
        _print_receipt(receipt)
    raise typer.Exit(0)


@receipt_app.command("operations")
def receipt_operations_cmd(
    target: Annotated[str | None, typer.Option("--target")] = None,
    config_dir: Annotated[Path | None, typer.Option("--config-dir")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List receipts, newest first."""
    from superrobot.pipeline.receipts import list_receipts

    receipts = list_receipts(config_dir, target=target)
    if json_out:
        console.print_json(json.dumps([r.model_dump() for r in receipts]))
        raise typer.Exit(0)
    if not receipts:
        console.print("[yellow]No receipts found[/]")
        raise typer.Exit(0)
    table = Table(title="SuperRobot receipts")
    table.add_column("id")
    table.add_column("target")
    table.add_column("action")
    table.add_column("status")
    table.add_column("created_at")
    for r in receipts:
        table.add_row(
            r.id, r.target, r.action, "[green]ok[/]" if r.success else "[red]fail[/]", r.created_at
        )
    console.print(table)
    raise typer.Exit(0)


@receipt_app.command("diagnose")
def receipt_diagnose_cmd(
    receipt_id: Annotated[str, typer.Argument()],
    config_dir: Annotated[Path | None, typer.Option("--config-dir")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Pattern-matched fix suggestion for a failed receipt."""
    from superrobot.pipeline.receipts import diagnose, load_receipt

    receipt = load_receipt(receipt_id, config_dir)
    if receipt is None:
        console.print(f"[red]No such receipt[/] {receipt_id!r}")
        raise typer.Exit(1)
    fix = diagnose(receipt)
    if json_out:
        console.print_json(json.dumps({"receipt_id": receipt.id, "diagnosis": fix}))
    else:
        console.print(fix)
    raise typer.Exit(0)


@receipt_app.command("replace")
def receipt_replace_cmd(
    receipt_id: Annotated[str, typer.Argument()],
    secret: Annotated[
        list[str] | None,
        typer.Option("--secret", help="KEY=credential:<id> (workload target, repeatable)"),
    ] = None,
    waive: Annotated[
        bool, typer.Option("--waive", help="Proceed despite blocking Gap Analysis findings")
    ] = False,
    config_dir: Annotated[Path | None, typer.Option("--config-dir")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Re-run the deploy captured by a receipt; the new receipt references it."""
    from superrobot.pipeline.receipts import load_receipt

    receipt = load_receipt(receipt_id, config_dir)
    if receipt is None:
        console.print(f"[red]No such receipt[/] {receipt_id!r}")
        raise typer.Exit(1)
    if not receipt.manifest_dir:
        console.print("[red]Receipt has no manifest_dir recorded — cannot replay[/]")
        raise typer.Exit(1)
    path = Path(receipt.manifest_dir)

    if receipt.target == "agent-app":
        code = asyncio.run(
            _deploy_agent_app(
                path,
                has_ui=receipt.has_ui,
                waive=waive,
                config_dir=config_dir,
                json_out=json_out,
                replaces=receipt.id,
            )
        )
    else:
        if not receipt.image_uri:
            console.print(
                "[red]Receipt has no image_uri recorded — cannot replay workload deploy[/]"
            )
            raise typer.Exit(1)
        code = asyncio.run(
            _deploy_workload(
                path,
                image_uri=receipt.image_uri,
                secrets=secret,
                waive=waive,
                config_dir=config_dir,
                json_out=json_out,
                replaces=receipt.id,
            )
        )
    raise typer.Exit(code)


if __name__ == "__main__":
    app()
