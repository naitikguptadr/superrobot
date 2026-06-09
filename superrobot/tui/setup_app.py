"""Interactive setup TUI — first-run wizard."""

from __future__ import annotations

import os

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Button, Footer, Header, Input, Static

from superrobot import __version__
from superrobot.env import write_env_file
from superrobot.setup.checks import SetupCheckResult, run_all_checks
from superrobot.setup.runner import SetupRunner
from superrobot.setup.state import mark_setup_complete

SETUP_STEPS = ["Welcome", "Tools", "Auth", "Credentials", "Verify", "Done"]


class SetupApp(App[None]):
    """Full-screen setup wizard."""

    CSS = """
    SetupApp {
        align: center middle;
    }
    #setup-container {
        width: 80;
        height: auto;
        max-height: 90%;
        border: thick $accent;
        padding: 1 2;
        background: $surface;
    }
    #step-indicator {
        text-align: center;
        margin-bottom: 1;
    }
    #step-content {
        height: auto;
        margin: 1 0;
    }
    .setup-btn {
        margin: 1 0;
    }
    #endpoint-input, #token-input, #model-input {
        margin: 1 0;
    }
    """

    step: reactive[int] = reactive(0)
    check_result: reactive[SetupCheckResult | None] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="setup-container"):
            yield Static(f"[bold cyan]SuperRobot Setup[/] v{__version__}", id="title")
            yield Static(id="step-indicator")
            yield Static(id="step-content")
            yield Button("Continue", variant="primary", id="continue-btn", classes="setup-btn")
            yield Button("Run dr auth login", id="auth-btn", classes="setup-btn")
            yield Input(
                placeholder="https://app.datarobot.com",
                id="endpoint-input",
            )
            yield Input(placeholder="API token", id="token-input", password=True)
            yield Input(
                placeholder="azure/gpt-4o-2024-11-20",
                id="model-input",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#auth-btn", Button).display = False
        self.query_one("#endpoint-input", Input).display = False
        self.query_one("#token-input", Input).display = False
        self.query_one("#model-input", Input).display = False
        self._refresh_step()

    def watch_step(self, _value: int) -> None:
        self._refresh_step()

    def _refresh_step(self) -> None:
        indicator = self.query_one("#step-indicator", Static)
        content = self.query_one("#step-content", Static)
        continue_btn = self.query_one("#continue-btn", Button)
        auth_btn = self.query_one("#auth-btn", Button)
        endpoint_input = self.query_one("#endpoint-input", Input)
        token_input = self.query_one("#token-input", Input)
        model_input = self.query_one("#model-input", Input)

        step_names = " → ".join(
            f"[bold]{s}[/]" if i == self.step else s for i, s in enumerate(SETUP_STEPS)
        )
        indicator.update(step_names)

        auth_btn.display = False
        endpoint_input.display = False
        token_input.display = False
        model_input.display = False
        continue_btn.label = "Continue"

        if self.step == 0:
            content.update(
                "Welcome! This wizard configures everything you need to migrate\n"
                "Python agents to DataRobot.\n\n"
                "We'll check tools, authenticate, and verify LLM Gateway access."
            )
        elif self.step == 1:
            content.update(self._format_prereqs())
            continue_btn.label = "Re-check" if self.check_result else "Check tools"
        elif self.step == 2:
            result = self.check_result
            if result and result.auth_ok:
                content.update("[green]✓ dr auth check passed[/]")
            else:
                content.update(
                    "[yellow]Not authenticated with DataRobot.[/]\n"
                    "Click below to run [bold]dr auth login[/] in your terminal."
                )
                auth_btn.display = True
        elif self.step == 3:
            content.update(
                "Enter your DataRobot credentials.\n"
                "[dim]Use Platform API URL — NOT the prediction URL.[/]"
            )
            endpoint_input.display = True
            token_input.display = True
            model_input.display = True
            endpoint_input.value = os.environ.get("DATAROBOT_ENDPOINT", "https://app.datarobot.com")
            model_input.value = os.environ.get("SUPERROBOT_MODEL", "azure/gpt-4o-2024-11-20")
        elif self.step == 4:
            content.update("[bold]Testing LLM Gateway connectivity...[/]")
            continue_btn.label = "Testing..."
            continue_btn.disabled = True
            self.run_gateway_check()
        elif self.step == 5:
            content.update(
                "[bold green]Setup complete![/]\n\n"
                "Run [bold]superrobot import <path>[/] to migrate an agent.\n"
                "Press Continue to exit."
            )
            continue_btn.label = "Exit"

    def _format_prereqs(self) -> str:
        if not self.check_result:
            return "Click [bold]Check tools[/] to verify prerequisites."
        lines = []
        for p in self.check_result.prerequisites:
            icon = "[green]✓[/]" if p.installed else "[red]✗[/]"
            hint = f" — {p.install_hint}" if not p.installed else ""
            lines.append(f"{icon} {p.name}{hint}")
        if self.check_result.prerequisites_ok:
            lines.append("\n[green]All prerequisites installed.[/]")
        else:
            lines.append("\n[yellow]Install missing tools, then re-check.[/]")
        return "\n".join(lines)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "auth-btn":
            self.run_auth_login()
        elif event.button.id == "continue-btn":
            self._advance()

    def _advance(self) -> None:
        if self.step == 0:
            self.step = 1
            self.run_prereq_check()
        elif self.step == 1:
            if self.check_result and self.check_result.prerequisites_ok:
                self.step = 2
                self.run_auth_check()
            else:
                self.run_prereq_check()
        elif self.step == 2:
            if self.check_result and self.check_result.auth_ok:
                self.step = 3
            else:
                self.run_auth_check()
        elif self.step == 3:
            self._save_credentials()
            self.step = 4
        elif self.step == 5:
            self.exit()
        # step 4 handled by gateway check callback

    def _save_credentials(self) -> None:
        endpoint = self.query_one("#endpoint-input", Input).value.strip()
        token = self.query_one("#token-input", Input).value.strip()
        model = self.query_one("#model-input", Input).value.strip() or "azure/gpt-4o-2024-11-20"
        write_env_file(
            {
                "DATAROBOT_ENDPOINT": endpoint,
                "DATAROBOT_API_TOKEN": token,
                "SUPERROBOT_MODEL": model,
            }
        )

    @work(exclusive=True)
    async def run_prereq_check(self) -> None:
        result = await run_all_checks()
        self.check_result = result
        self._refresh_step()

    @work(exclusive=True)
    async def run_auth_check(self) -> None:
        from superrobot.setup.checks import check_auth

        if self.check_result:
            auth_ok = await check_auth()
            updated = self.check_result
            updated.auth_ok = auth_ok
            self.check_result = updated
        self._refresh_step()

    @work(exclusive=True)
    async def run_auth_login(self) -> None:
        runner = SetupRunner()
        ok = await runner._run_auth_login()
        if self.check_result:
            updated = self.check_result
            updated.auth_ok = ok
            self.check_result = updated
        self._refresh_step()

    @work(exclusive=True)
    async def run_gateway_check(self) -> None:
        from superrobot.setup.checks import check_gateway

        ok, error = await check_gateway()
        content = self.query_one("#step-content", Static)
        continue_btn = self.query_one("#continue-btn", Button)
        continue_btn.disabled = False
        continue_btn.label = "Continue"

        if ok:
            endpoint = os.environ.get("DATAROBOT_ENDPOINT", "")
            model = os.environ.get("SUPERROBOT_MODEL", "azure/gpt-4o-2024-11-20")
            mark_setup_complete(endpoint, model=model)
            content.update("[green]✓ LLM Gateway reachable[/]")
            self.step = 5
        else:
            content.update(
                f"[red]Gateway check failed:[/] {error}\n\n[yellow]Fix credentials and retry.[/]"
            )
            continue_btn.label = "Retry"
            self.step = 3
