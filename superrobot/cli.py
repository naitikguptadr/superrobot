"""Typer entry point — arg parsing and TUI launch."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from superrobot import __version__
from superrobot.app import SuperRobotApp
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
from superrobot.startup import check_prerequisites, print_missing_prerequisites

app = typer.Typer(
    name="superrobot",
    help="Bring any Python agent to DataRobot without rebuilding it from scratch.",
    no_args_is_help=True,
)


def _ensure_prerequisites() -> None:
    missing = check_prerequisites()
    if missing:
        print_missing_prerequisites(missing)


def _ensure_auth(no_tui: bool) -> None:
    async def _check() -> bool:
        from superrobot.startup import check_auth

        return await check_auth()

    if not asyncio.run(_check()):
        msg = "Auth failed. Run: dr auth login"
        if no_tui:
            print(msg, file=sys.stderr)
        else:
            typer.echo(msg, err=True)
        raise typer.Exit(1)


@app.command()
def import_cmd(
    source: Annotated[str, typer.Argument(help="GitHub URL or local path")],
    skip_eval: Annotated[bool, typer.Option("--skip-eval")] = False,
    output_dir: Annotated[str | None, typer.Option("--output-dir")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    debug: Annotated[bool, typer.Option("--debug")] = False,
    no_tui: Annotated[bool, typer.Option("--no-tui")] = False,
) -> None:
    """Brownfield import: full Scan → Deploy pipeline."""
    _ensure_prerequisites()
    _ensure_auth(no_tui)
    if debug:
        import os

        os.environ["SUPERROBOT_DEBUG"] = "1"
    if model:
        import os

        os.environ["SUPERROBOT_MODEL"] = model

    repo_path = _resolve_source(source)
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
) -> None:
    """Greenfield: wizard → generate → deploy."""
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
) -> None:
    """Browse DR templates → customize → deploy."""
    _ensure_prerequisites()
    _ensure_auth(no_tui)
    if no_tui:
        typer.echo("Template mode requires TUI for template browser.")
        raise typer.Exit(1)
    SuperRobotApp(mode="template", skip_eval=skip_eval).run()


@app.command()
def scan_cmd(
    path: Annotated[str, typer.Argument(help="Local repo path")],
) -> None:
    """Stage 1 only: output ScanResult JSON."""
    result = scan(path)
    typer.echo(result.model_dump_json(indent=2))


@app.command()
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


@app.command()
def eval_cmd(
    path: Annotated[str, typer.Option("--path", "-p")] = ".",
) -> None:
    """Run 5-shot pre-deploy eval."""
    scan_result = scan(path)
    analysis_result = asyncio.run(analyze(scan_result))
    summary = asyncio.run(run_eval(analysis_result, cwd=path))
    typer.echo(summary.model_dump_json(indent=2))


@app.command()
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
def live() -> None:
    """Attach to deployed agent and show live graph."""
    typer.echo("Live mode: connect to deployed agent (not yet implemented)")


@app.command()
def diff(
    config_a: Annotated[str, typer.Argument()],
    config_b: Annotated[str, typer.Argument()],
) -> None:
    """Compare two agent configs side by side."""
    a = Path(config_a).read_text()
    b = Path(config_b).read_text()
    if a == b:
        typer.echo("Configs are identical")
    else:
        typer.echo(f"Configs differ ({len(a)} vs {len(b)} chars)")


@app.callback()
def main(
    version: Annotated[bool, typer.Option("--version", "-V")] = False,
) -> None:
    if version:
        typer.echo(f"superrobot {__version__}")
        raise typer.Exit()


ui_app = typer.Typer(help="dr-ui component builder")
app.add_typer(ui_app, name="ui")


@ui_app.command("add")
def ui_add(
    description: Annotated[str, typer.Argument(help="Component description")],
    path: Annotated[str, typer.Option("--path", "-p")] = ".",
) -> None:
    """Generate a dr-ui component from description."""
    _ensure_prerequisites()
    scan_result = scan(path)
    analysis_result = asyncio.run(analyze(scan_result))
    tsx = asyncio.run(generate_ui_component(description, analysis_result))
    typer.echo(tsx)


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
        summary = await run_eval(analysis_result, cwd=str(out))
        print(json.dumps({"stage": "eval", "result": summary.model_dump()}, indent=2))


def _resolve_source(source: str) -> str:
    path = Path(source)
    if path.exists():
        return str(path.resolve())
    if source.startswith("http"):
        typer.echo(f"GitHub clone not yet implemented; use local path. Got: {source}", err=True)
        raise typer.Exit(1)
    if not path.exists():
        typer.echo(f"Path not found: {source}", err=True)
        raise typer.Exit(1)
    return str(path.resolve())


if __name__ == "__main__":
    app()
