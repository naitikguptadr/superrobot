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
from superrobot.setup.checks import (
    SetupCheckResult,
    auth_matches_endpoint,
    run_all_checks,
)
from superrobot.setup.constants import (
    DEFAULT_MODEL,
    ENDPOINT_PRESETS,
    api_endpoint,
    endpoint_label,
    normalize_endpoint,
)
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

        # Step 2: Environment (selected first so auth targets the right URL)
        c.print("\n[bold]Step 2/4[/] — Environment variables")
        endpoint, token, model = self._prompt_environment()
        write_env_file(
            {
                "DATAROBOT_ENDPOINT": endpoint,
                "DATAROBOT_API_TOKEN": token,
                "SUPERROBOT_MODEL": model,
            }
        )
        result.endpoint_set = True
        result.token_set = True
        c.print("[green]✓[/] Saved to ~/.config/superrobot/.env")

        # Step 3: Auth against the selected environment
        c.print(f"\n[bold]Step 3/4[/] — DataRobot authentication ({endpoint_label(endpoint)})")
        needs_login = not result.auth_ok or not auth_matches_endpoint(endpoint)
        if needs_login:
            if result.auth_ok:
                c.print(
                    f"[yellow]dr is authenticated against a different environment.[/] "
                    f"Re-authenticating against [bold]{endpoint}[/]..."
                )
            else:
                c.print(
                    f"[yellow]Not authenticated.[/] Launching [bold]dr auth login {endpoint}[/]..."
                )
            login_ok = await self._run_auth_login(endpoint)
            if not login_ok:
                c.print(f"[red]Authentication failed. Run:[/] dr auth login {endpoint}")
                return result
            result.auth_ok = True
        else:
            c.print(f"[green]✓[/] dr auth check passed ({endpoint})")

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
                c.print("[yellow]You can retry later with:[/] superrobot setup --check")
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
        c.print("\n[bold]DataRobot environment[/]")
        c.print("  [1] Production — https://app.datarobot.com")
        c.print("  [2] Staging    — https://staging.datarobot.com")
        c.print("  [3] Custom URL")
        choice = Prompt.ask("Select", choices=["1", "2", "3"], default="1", console=c)
        if choice == "2":
            endpoint = ENDPOINT_PRESETS["staging"]
        elif choice == "3":
            default_endpoint = os.environ.get("DATAROBOT_ENDPOINT", ENDPOINT_PRESETS["production"])
            endpoint = Prompt.ask(
                "DataRobot Platform API URL (NOT prediction URL)",
                default=default_endpoint,
                console=c,
            )
        else:
            endpoint = ENDPOINT_PRESETS["production"]
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
            default=os.environ.get("SUPERROBOT_MODEL", DEFAULT_MODEL),
            console=c,
        )
        return api_endpoint(endpoint), token.strip(), model.strip()

    async def _run_auth_login(self, endpoint: str | None = None) -> bool:
        """Run dr auth login with inherited stdio, targeting the given endpoint URL."""
        args = ["auth", "login"]
        if endpoint:
            args.append(normalize_endpoint(endpoint))
        proc = await asyncio.create_subprocess_exec(
            "dr",
            *args,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        await proc.wait()
        if proc.returncode != 0:
            return False
        return await self.cli.auth_check() if self.cli else False
