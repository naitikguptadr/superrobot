"""Typer entry point — arg parsing and TUI launch."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from superrobot import __version__
from superrobot.app import SuperRobotApp
from superrobot.env import load_user_env
from superrobot.live import run_live_query
from superrobot.pipeline.analyzer import analyze
from superrobot.pipeline.config_generator import (
    generate_config,
    render_files,
    write_generated_files,
)
from superrobot.pipeline.deployer import deploy
from superrobot.pipeline.evaluator import run_eval
from superrobot.pipeline.scanner import scan
from superrobot.pipeline.ui_generator import generate_ui_component
from superrobot.repo import clone_repository
from superrobot.setup.checks import run_all_checks
from superrobot.setup.runner import SetupRunner
from superrobot.setup.state import is_setup_complete
from superrobot.startup import check_prerequisites, print_missing_prerequisites
from superrobot.tui.setup_app import SetupApp

load_user_env()

app = typer.Typer(
    name="superrobot",
    help="Bring any Python agent to DataRobot without rebuilding it from scratch.",
    no_args_is_help=True,
)

console = Console()


def _ensure_prerequisites() -> None:
    missing = check_prerequisites()
    if missing:
        print_missing_prerequisites(missing)


def _ensure_auth(no_tui: bool) -> None:
    async def _check() -> bool:
        from superrobot.startup import check_auth

        return await check_auth()

    if not asyncio.run(_check()):
        msg = "Auth failed. Run: dr auth login  (or: superrobot setup)"
        if no_tui:
            print(msg, file=sys.stderr)
        else:
            typer.echo(msg, err=True)
        raise typer.Exit(1)


def _ensure_setup(*, strict: bool = True) -> None:
    """Warn or exit if setup has not been completed."""
    if is_setup_complete():
        return
    msg = "Setup incomplete. Run: superrobot setup"
    if strict:
        console.print(f"[yellow]{msg}[/]")
        raise typer.Exit(1)
    console.print(f"[dim]{msg}[/]")


@app.command("setup")
def setup_cmd(
    check_only: Annotated[bool, typer.Option("--check", help="Verify setup only")] = False,
    no_tui: Annotated[
        bool, typer.Option("--no-tui", help="Use Rich prompts instead of TUI")
    ] = False,
    skip_gateway: Annotated[
        bool, typer.Option("--skip-gateway", help="Skip LLM gateway test")
    ] = False,
) -> None:
    """Interactive first-run setup — tools, auth, credentials, gateway verify."""
    if check_only:
        result = asyncio.run(run_all_checks())
        _print_setup_status(result)
        raise typer.Exit(0 if result.is_ready else 1)

    if no_tui:
        result = asyncio.run(SetupRunner(console=console).run(skip_gateway=skip_gateway))
        raise typer.Exit(0 if result.is_ready else 1)

    SetupApp().run()


def _print_setup_status(result: object) -> None:
    from superrobot.setup.checks import SetupCheckResult

    assert isinstance(result, SetupCheckResult)
    table_items = [
        ("Prerequisites", result.prerequisites_ok),
        ("dr auth", result.auth_ok),
        ("Environment", result.env_ok),
        ("LLM Gateway", result.gateway_ok),
    ]
    for label, ok in table_items:
        icon = "[green]✓[/]" if ok else "[red]✗[/]"
        console.print(f"  {icon} {label}")
    if result.gateway_error:
        console.print(f"  [dim]Gateway: {result.gateway_error}[/]")
    if result.is_ready:
        console.print("\n[green]Setup complete — ready to use SuperRobot.[/]")
    else:
        console.print("\n[yellow]Run superrobot setup to finish configuration.[/]")


@app.command("import")
def import_cmd(
    source: Annotated[str, typer.Argument(help="GitHub URL or local path")],
    skip_eval: Annotated[bool, typer.Option("--skip-eval")] = False,
    output_dir: Annotated[str | None, typer.Option("--output-dir", "-o")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    debug: Annotated[bool, typer.Option("--debug")] = False,
    no_tui: Annotated[bool, typer.Option("--no-tui")] = False,
    skip_setup_check: Annotated[bool, typer.Option("--skip-setup-check")] = False,
    skip_auth_check: Annotated[
        bool, typer.Option("--skip-auth-check", help="Skip dr auth (local/CI only)")
    ] = False,
) -> None:
    """Brownfield import: full Scan → Deploy pipeline."""
    if not skip_setup_check:
        _ensure_setup()
    _ensure_prerequisites()
    if not skip_auth_check:
        _ensure_auth(no_tui)
    if debug:
        import os

        os.environ["SUPERROBOT_DEBUG"] = "1"
    if model:
        import os

        os.environ["SUPERROBOT_MODEL"] = model

    repo_path = asyncio.run(_resolve_source(source))
    if no_tui:
        asyncio.run(_run_import_headless(repo_path, output_dir, skip_eval))
        return

    SuperRobotApp(
        repo_path=repo_path,
        mode="import",
        skip_eval=skip_eval,
        output_dir=output_dir,
    ).run()


@app.command("new")
def new_cmd(
    skip_eval: Annotated[bool, typer.Option("--skip-eval")] = False,
    no_tui: Annotated[bool, typer.Option("--no-tui")] = False,
    skip_setup_check: Annotated[bool, typer.Option("--skip-setup-check")] = False,
) -> None:
    """Greenfield: wizard → generate → deploy."""
    if not skip_setup_check:
        _ensure_setup()
    _ensure_prerequisites()
    _ensure_auth(no_tui)
    if no_tui:
        typer.echo("Greenfield mode requires TUI for wizard questions.")
        raise typer.Exit(1)
    SuperRobotApp(mode="greenfield", skip_eval=skip_eval).run()


@app.command()
def template(
    skip_eval: Annotated[bool, typer.Option("--skip-eval")] = False,
    no_tui: Annotated[bool, typer.Option("--no-tui")] = False,
    skip_setup_check: Annotated[bool, typer.Option("--skip-setup-check")] = False,
) -> None:
    """Browse DR templates → customize → deploy."""
    if not skip_setup_check:
        _ensure_setup()
    _ensure_prerequisites()
    _ensure_auth(no_tui)
    if no_tui:
        typer.echo("Template mode requires TUI for template browser.")
        raise typer.Exit(1)
    SuperRobotApp(mode="template", skip_eval=skip_eval).run()


@app.command("scan")
def scan_cmd(
    path: Annotated[str, typer.Argument(help="Local repo path")],
) -> None:
    """Stage 1 only: output ScanResult JSON."""
    result = scan(path)
    typer.echo(result.model_dump_json(indent=2))


@app.command("analyze")
def analyze_cmd(
    path: Annotated[str, typer.Argument(help="Local repo path")],
) -> None:
    """Stages 1-2: output AnalysisResult JSON."""
    scan_result = scan(path)
    result = asyncio.run(analyze(scan_result))
    typer.echo(result.model_dump_json(indent=2))


@app.command()
def generate(
    path: Annotated[str, typer.Argument(help="Local repo path")],
    output_dir: Annotated[str, typer.Option("--output-dir", "-o")] = "./output",
) -> None:
    """Stages 1-3: write generated files to --output-dir."""
    scan_result = scan(path)
    analysis_result = asyncio.run(analyze(scan_result))
    config = generate_config(scan_result, analysis_result)
    files = render_files(config)
    out = write_generated_files(files, output_dir)
    typer.echo(f"Generated files written to {out}")


@app.command("eval")
def eval_cmd(
    path: Annotated[str, typer.Option("--path", "-p")] = ".",
    bundle: Annotated[
        str | None,
        typer.Option("--bundle", help="Generated bundle dir to eval (default: <path>/.superrobot)"),
    ] = None,
) -> None:
    """Run 5-shot pre-deploy eval."""
    scan_result = scan(path)
    analysis_result = asyncio.run(analyze(scan_result))
    cwd = bundle or str(Path(path) / ".superrobot")
    if not Path(cwd).exists():
        cwd = path
    summary = asyncio.run(
        run_eval(analysis_result, cwd=cwd, entry=_entry_info_from_scan(scan_result))
    )
    typer.echo(summary.model_dump_json(indent=2))


def _entry_info_from_scan(scan_result: object) -> tuple[str, str, list[str]] | None:
    from superrobot.models.agent_config import parse_signature_params
    from superrobot.models.scan_result import ScanResult
    from superrobot.pipeline.config_generator import flat_module_name

    assert isinstance(scan_result, ScanResult)
    if not scan_result.entry_points:
        return None
    ep = scan_result.entry_points[0]
    module = flat_module_name(scan_result.repo_path, ep.file)
    return (module, ep.function, parse_signature_params(ep.signature))


@app.command("deploy")
def deploy_cmd(
    path: Annotated[str, typer.Option("--path", "-p")] = ".",
) -> None:
    """Run deploy against current generated config."""
    result = asyncio.run(deploy(cwd=path))
    if not result.success:
        typer.echo(result.error_message or "Deploy failed", err=True)
        raise typer.Exit(1)
    typer.echo("Deploy succeeded")


@app.command()
def live(
    path: Annotated[str, typer.Option("--path", "-p", help="Agent project directory")] = ".",
    query: Annotated[str, typer.Option("--query", "-q")] = "Hello, test query",
) -> None:
    """Attach to locally running agent and show execution result."""
    _ensure_prerequisites()
    result = asyncio.run(run_live_query(query, cwd=path))
    if result.success:
        typer.echo(result.output)
        typer.echo(f"\nExecution path: {' → '.join(result.active_nodes)}", err=True)
    else:
        typer.echo(result.stderr or "Live run failed", err=True)
        raise typer.Exit(1)


@app.command()
def diff(
    config_a: Annotated[str, typer.Argument()],
    config_b: Annotated[str, typer.Argument()],
) -> None:
    """Compare two agent configs side by side."""
    import difflib

    for p in (config_a, config_b):
        if not Path(p).is_file():
            raise typer.BadParameter(f"File not found: {p}")
    a = Path(config_a).read_text()
    b = Path(config_b).read_text()
    if a == b:
        typer.echo("Configs are identical")
        return
    diff_lines = difflib.unified_diff(
        a.splitlines(), b.splitlines(), fromfile=config_a, tofile=config_b, lineterm=""
    )
    for line in diff_lines:
        typer.echo(line)
    raise typer.Exit(1)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"superrobot {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", "-V", callback=_version_callback, is_eager=True),
    ] = False,
) -> None:
    """SuperRobot — bring any Python agent to DataRobot."""


ui_app = typer.Typer(help="dr-ui component builder")
app.add_typer(ui_app, name="ui")


@ui_app.command("add")
def ui_add(
    description: Annotated[str, typer.Argument(help="Component description")],
    path: Annotated[str, typer.Option("--path", "-p")] = ".",
    preview: Annotated[
        bool, typer.Option("--preview", help="Write ui/preview.html and open it in a browser")
    ] = False,
    output_dir: Annotated[str | None, typer.Option("--output-dir", "-o")] = None,
) -> None:
    """Generate a dr-ui component from description."""
    _ensure_prerequisites()
    scan_result = scan(path)
    analysis_result = asyncio.run(analyze(scan_result))
    tsx = asyncio.run(generate_ui_component(description, analysis_result))
    typer.echo(tsx)
    if preview or output_dir:
        import webbrowser

        from superrobot.pipeline.ui_preview import write_preview

        out = output_dir or path
        (Path(out) / "ui").mkdir(parents=True, exist_ok=True)
        (Path(out) / "ui" / "component.tsx").write_text(tsx)
        preview_path = write_preview(tsx, out)
        typer.echo(f"\nLive preview: {preview_path}", err=True)
        if preview:
            webbrowser.open(f"file://{preview_path}")


async def _run_import_headless(repo_path: str, output_dir: str | None, skip_eval: bool) -> None:
    scan_result = scan(repo_path)
    print(json.dumps({"stage": "scan", "result": scan_result.model_dump()}, indent=2))

    analysis_result = await analyze(scan_result)
    print(json.dumps({"stage": "analyze", "result": analysis_result.model_dump()}, indent=2))

    config = generate_config(scan_result, analysis_result)
    files = render_files(config)
    out = write_generated_files(files, output_dir or f"{repo_path}/.superrobot")
    generate_payload = {
        "stage": "generate",
        "output_dir": str(out),
        "files": list(files.keys()),
    }
    print(json.dumps(generate_payload, indent=2))

    if not skip_eval:
        summary = await run_eval(
            analysis_result, cwd=str(out), entry=_entry_info_from_scan(scan_result)
        )
        print(json.dumps({"stage": "eval", "result": summary.model_dump()}, indent=2))


async def _resolve_source(source: str) -> str:
    path = Path(source)
    if path.exists():
        return str(path.resolve())
    if source.startswith("http") or "github.com" in source:
        cloned = await clone_repository(source)
        return str(cloned)
    msg = f"Path not found: {source}"
    raise typer.BadParameter(msg)


if __name__ == "__main__":
    app()
