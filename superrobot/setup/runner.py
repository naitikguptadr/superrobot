"""Setup orchestration — headless and TUI-backed."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from enum import StrEnum

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from superrobot.dr.cli_wrapper import DrCliWrapper
from superrobot.env import load_user_env, write_env_file
from superrobot.setup.checks import SetupCheckResult, run_all_checks
from superrobot.setup.state import mark_setup_complete


class SetupStep(StrEnum):
    """Setup wizard steps."""

    WELCOME = "welcome"
    PREREQUISITES = "prerequisites"
    AUTH = "auth"
    ENVIRONMENT = "environment"
    GATEWAY = "gateway"
    COMPLETE = "complete"


@dataclass
class SetupRunner:
    """Runs the interactive setup flow."""

    console: Console | None = None
    cli: DrCliWrapper | None = None

    def __post_init__(self) -> None:
        self.console = self.console or Console()
        self.cli = self.cli or DrCliWrapper()

    async def run(self, *, skip_gateway: bool = False) -> SetupCheckResult:
        """Run full headless setup with Rich prompts."""
        load_user_env()
        c = self.console
        assert c is not None

        c.print(
            Panel.fit(
                "[bold cyan]SuperRobot Setup[/]\n"
                "Bring any Python agent to DataRobot without rebuilding it from scratch.\n"
                "This wizard configures everything you need in a few steps.",
                border_style="cyan",
            )
        )

        # Step 1: Prerequisites
        c.print("\n[bold]Step 1/4[/] — Prerequisites")
        result = await run_all_checks(self.cli)
        self._print_prerequisites(result)

        if not result.prerequisites_ok:
            c.print(
                "\n[yellow]Install missing tools above, then re-run:[/] [bold]superrobot setup[/]"
            )
            return result

        # Step 2: Auth
        c.print("\n[bold]Step 2/4[/] — DataRobot authentication")
        if not result.auth_ok:
            c.print("[yellow]Not authenticated.[/] Launching [bold]dr auth login[/]...")
            login_ok = await self._run_auth_login()
            if not login_ok:
                c.print("[red]Authentication failed. Run:[/] dr auth login")
                return result
            result.auth_ok = True
        else:
            c.print("[green]✓[/] dr auth check passed")

        # Step 3: Environment
        c.print("\n[bold]Step 3/4[/] — Environment variables")
        endpoint, token, model = self._prompt_environment()
        write_env_file(
            {
                "DATAROBOT_ENDPOINT": endpoint,
                "DATAROBOT_API_TOKEN": token,
                "SUPERROBOT_MODEL": model,
            }
        )
        os.environ["DATAROBOT_ENDPOINT"] = endpoint
        os.environ["DATAROBOT_API_TOKEN"] = token
        os.environ["SUPERROBOT_MODEL"] = model
        result.endpoint_set = True
        result.token_set = True
        c.print("[green]✓[/] Saved to ~/.config/superrobot/.env")

        # Step 4: Gateway verify
        c.print("\n[bold]Step 4/4[/] — LLM Gateway connectivity")
        if skip_gateway:
            c.print("[dim]Skipped gateway check (--skip-gateway)[/]")
            result.gateway_ok = True
        else:
            with c.status("[bold]Testing LLM Gateway..."):
                from superrobot.setup.checks import check_gateway

                result.gateway_ok, result.gateway_error = await check_gateway()
            if result.gateway_ok:
                c.print("[green]✓[/] LLM Gateway reachable")
            else:
                c.print(f"[red]✗[/] Gateway check failed: {result.gateway_error}")
                c.print("[yellow]You can retry later with:[/] superrobot setup check")
                return result

        mark_setup_complete(endpoint, model=model)
        c.print(
            Panel.fit(
                "[bold green]Setup complete![/]\n\n"
                "Next steps:\n"
                "  [bold]superrobot import ./your-agent[/]  — migrate an existing agent\n"
                "  [bold]superrobot new[/]                  — build from scratch\n"
                "  [bold]superrobot template[/]              — start from a DR template",
                border_style="green",
            )
        )
        return result

    def _print_prerequisites(self, result: SetupCheckResult) -> None:
        c = self.console
        assert c is not None
        table = Table(show_header=True, header_style="bold")
        table.add_column("Tool", style="cyan")
        table.add_column("Status")
        table.add_column("Install")
        for prereq in result.prerequisites:
            status = "[green]✓ installed[/]" if prereq.installed else "[red]✗ missing[/]"
            table.add_row(prereq.name, status, prereq.install_hint if not prereq.installed else "")
        c.print(table)

    def _prompt_environment(self) -> tuple[str, str, str]:
        c = self.console
        assert c is not None
        default_endpoint = os.environ.get("DATAROBOT_ENDPOINT", "https://app.datarobot.com")
        endpoint = Prompt.ask(
            "DataRobot Platform API URL (NOT prediction URL)",
            default=default_endpoint,
            console=c,
        )
        token = os.environ.get("DATAROBOT_API_TOKEN", "")
        if token:
            use_existing = Confirm.ask(
                "Use existing DATAROBOT_API_TOKEN from environment?",
                console=c,
            )
            if not use_existing:
                token = ""
        if not token:
            token = Prompt.ask("DataRobot API token", password=True, console=c)
        model = Prompt.ask(
            "LLM model",
            default=os.environ.get("SUPERROBOT_MODEL", "azure/gpt-4o-2024-11-20"),
            console=c,
        )
        return endpoint.strip(), token.strip(), model.strip()

    async def _run_auth_login(self) -> bool:
        """Run dr auth login with inherited stdio."""
        proc = await asyncio.create_subprocess_exec(
            "dr",
            "auth",
            "login",
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        await proc.wait()
        if proc.returncode != 0:
            return False
        return await self.cli.auth_check() if self.cli else False
