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


if __name__ == "__main__":
    app()
