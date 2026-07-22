"""SuperRobot CLI — DataRobot-native control plane."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from superrobot import __version__
from superrobot.setup.doctor import run_doctor
from superrobot.setup.runner import run_setup

console = Console()
app = typer.Typer(
    name="superrobot",
    help="Bring any Python agent to DataRobot — migrate, validate, deploy, operate.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


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


@app.command("deploy")
def deploy_cmd(
    path: Annotated[Path, typer.Argument(help="Generated package directory")],
    target: Annotated[
        str,
        typer.Option("--target", help="Deploy target (agent-app only in Spec 04)"),
    ] = "agent-app",
    has_ui: Annotated[bool, typer.Option("--has-ui")] = False,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Deploy generated packaging to DataRobot Agent App via `dr`."""
    from superrobot.pipeline.deployer import DEPLOY_WARNINGS, deploy

    if target != "agent-app":
        console.print(
            f"[red]Unsupported target[/] {target!r} — Spec 04 supports [cyan]agent-app[/] only"
        )
        raise typer.Exit(2)
    if not path.is_dir():
        console.print(f"[red]Not a directory[/] {path}")
        raise typer.Exit(2)

    for warning in DEPLOY_WARNINGS:
        if not has_ui and "Frontend" in warning:
            continue
        console.print(f"[yellow]![/] {warning}")

    result = asyncio.run(deploy(cwd=str(path), has_ui=has_ui))
    payload = {
        "success": result.success,
        "target": target,
        "warnings": result.warnings,
        "error_message": result.error_message,
    }
    if json_out:
        console.print_json(json.dumps(payload))
    elif result.success:
        console.print("[green]deploy succeeded[/]")
    else:
        console.print(f"[red]deploy failed[/] {result.error_message or ''}")
    raise typer.Exit(0 if result.success else 1)


if __name__ == "__main__":
    app()
