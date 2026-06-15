"""Full-screen keyboard-driven setup TUI."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from textual import events, work
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Grid, Vertical
from textual.widgets import DataTable, Footer, Input, RadioButton, RadioSet, Static

from superrobot import __version__
from superrobot.env import write_env_file
from superrobot.setup.checks import (
    SetupCheckResult,
    auth_matches_endpoint,
    check_auth,
    check_gateway,
    run_all_checks,
)
from superrobot.setup.constants import (
    DEFAULT_MODEL,
    ENDPOINT_PRESETS,
    api_endpoint,
    endpoint_label,
    normalize_endpoint,
)
from superrobot.setup.runner import SetupRunner
from superrobot.setup.state import mark_setup_complete
from superrobot.tui.setup_step_panel import SetupStepPanel

STEP_WELCOME = 0
STEP_TOOLS = 1
STEP_ENVIRONMENT = 2
STEP_AUTH = 3
STEP_VERIFY = 4
STEP_COMPLETE = 5

HINTS: dict[int, str] = {
    STEP_WELCOME: "[enter] continue  [q] quit",
    STEP_TOOLS: "[enter] continue  [r] recheck tools  [q] quit",
    STEP_ENVIRONMENT: "[enter] save & continue  [tab] next field  [q] quit",
    STEP_AUTH: "[enter] continue  [a] dr auth login  [r] recheck auth  [q] quit",
    STEP_VERIFY: "verifying gateway…",
    STEP_COMPLETE: "[enter] exit  [q] quit",
}


class SetupApp(App[None]):
    """Keyboard-first setup wizard — full screen, no clickable buttons."""

    CSS_PATH = str(Path(__file__).with_name("setup_app.css"))
    TITLE = "SuperRobot Setup"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("enter", "continue_step", "Continue", priority=True),
        Binding("a", "auth_login", "Auth", show=False),
        Binding("r", "recheck", "Recheck", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._step = 0
        self._check_result: SetupCheckResult | None = None
        self._gateway_running = False

    def compose(self) -> ComposeResult:
        yield Static(
            f"SUPERROBOT SETUP v{__version__}  |  [dim]keyboard-only — no mouse required[/]",
            id="setup-header",
        )
        with Grid(id="setup-grid"):
            yield SetupStepPanel(id="setup-step-panel")
            with Vertical(id="setup-detail"):
                yield Static("", id="setup-detail-title")
                yield Static("", id="setup-detail-body")
                with Vertical(id="setup-forms"):
                    yield DataTable(id="prereq-table", zebra_stripes=True)
                    with RadioSet(id="endpoint-presets"):
                        yield RadioButton(
                            f"Production — {ENDPOINT_PRESETS['production']}",
                            id="ep-production",
                        )
                        yield RadioButton(
                            f"Staging — {ENDPOINT_PRESETS['staging']}",
                            id="ep-staging",
                        )
                        yield RadioButton("Custom URL", id="ep-custom")
                    yield Static("Custom endpoint URL", classes="setup-field-label")
                    yield Input(placeholder="https://…", id="endpoint-custom")
                    yield Static("API token", classes="setup-field-label")
                    yield Input(placeholder="DATAROBOT_API_TOKEN", id="token-field", password=True)
                    yield Static("LLM model", classes="setup-field-label")
                    yield Input(placeholder=DEFAULT_MODEL, id="model-field")
        yield Static(HINTS[0], id="setup-hints")
        yield Footer()

    def on_mount(self) -> None:
        self._hide_forms()
        self._show_step(0)

    def _hide_forms(self) -> None:
        self.query_one("#prereq-table", DataTable).display = False
        self.query_one("#endpoint-presets", RadioSet).display = False
        self.query_one("#endpoint-custom", Input).display = False
        for node_id in ("#token-field", "#model-field"):
            self.query_one(node_id, Input).display = False
        for label in self.query(".setup-field-label"):
            label.display = False

    def _show_step(self, step: int) -> None:
        self._step = step
        panel = self.query_one(SetupStepPanel)
        panel.current_step = step
        panel.set_step_status(step, "active")
        self.query_one("#setup-hints", Static).update(HINTS.get(step, ""))

        title = self.query_one("#setup-detail-title", Static)
        body = self.query_one("#setup-detail-body", Static)
        self._hide_forms()
        if step != STEP_ENVIRONMENT:
            # A hidden-but-focused Input would swallow the a/r key bindings
            self.set_focus(None)

        if step == 0:
            title.update("Welcome")
            body.update(
                "Configure SuperRobot once, then migrate any Python agent to DataRobot.\n\n"
                "This wizard checks your tools, authenticates with DataRobot, saves\n"
                "credentials to [bold]~/.config/superrobot/.env[/], and verifies LLM Gateway.\n\n"
                "[bold]Press Enter to continue.[/]"
            )
        elif step == 1:
            title.update("Prerequisites")
            body.update("Checking required CLI tools…")
            self.run_prereq_check()
        elif step == STEP_ENVIRONMENT:
            title.update("Environment")
            body.update(
                "Select your DataRobot environment and enter credentials.\n"
                "[dim]Use the Platform API URL — never the prediction URL.[/]"
            )
            self._show_env_form()
        elif step == STEP_AUTH:
            title.update("Authentication")
            self._render_auth_step(body)
        elif step == 4:
            title.update("Verify")
            body.update("Pinging LLM Gateway…")
            self.run_gateway_check()
        elif step == 5:
            title.update("Complete")
            body.update(
                "[bold green]Setup complete.[/]\n\n"
                "  superrobot import <path>   — brownfield migrate\n"
                "  superrobot new             — greenfield wizard\n"
                "  superrobot template        — DR template browser"
            )

    def _show_env_form(self) -> None:
        self.query_one("#endpoint-presets", RadioSet).display = True
        self.query_one("#token-field", Input).display = True
        self.query_one("#model-field", Input).display = True
        for label in self.query(".setup-field-label"):
            label.display = True

        presets = self.query_one("#endpoint-presets", RadioSet)
        env_endpoint = normalize_endpoint(
            os.environ.get("DATAROBOT_ENDPOINT", ENDPOINT_PRESETS["production"])
        )
        if env_endpoint == ENDPOINT_PRESETS["staging"]:
            self._select_preset("ep-staging")
        elif env_endpoint in ENDPOINT_PRESETS.values():
            self._select_preset("ep-production")
        else:
            self._select_preset("ep-custom")
            self.query_one("#endpoint-custom", Input).value = env_endpoint

        token_field = self.query_one("#token-field", Input)
        token_field.value = os.environ.get("DATAROBOT_API_TOKEN", "")
        self.query_one("#model-field", Input).value = os.environ.get(
            "SUPERROBOT_MODEL", DEFAULT_MODEL
        )
        self._toggle_custom_endpoint()
        presets.focus()

    def _select_preset(self, button_id: str) -> None:
        for child in self.query_one("#endpoint-presets", RadioSet).children:
            if isinstance(child, RadioButton):
                child.value = child.id == button_id

    def on_radio_set_changed(self, _event: RadioSet.Changed) -> None:
        self._toggle_custom_endpoint()

    def _toggle_custom_endpoint(self) -> None:
        custom_btn = self.query_one("#ep-custom", RadioButton)
        custom_input = self.query_one("#endpoint-custom", Input)
        custom_input.display = custom_btn.value
        if custom_btn.value:
            custom_input.focus()

    def _selected_endpoint(self) -> str:
        """Endpoint chosen in the Environment step (already saved to env)."""
        return normalize_endpoint(
            os.environ.get("DATAROBOT_ENDPOINT", ENDPOINT_PRESETS["production"])
        )

    def _auth_ready(self) -> bool:
        return (
            self._check_result is not None
            and self._check_result.auth_ok
            and auth_matches_endpoint(self._selected_endpoint())
        )

    def _render_auth_step(self, body: Static) -> None:
        endpoint = self._selected_endpoint()
        env_name = endpoint_label(endpoint)
        if self._auth_ready():
            body.update(
                f"[green]✓[/] [bold]dr auth check passed[/] — {env_name} ({endpoint})\n\n"
                "Press [enter] to continue."
            )
        elif self._check_result and self._check_result.auth_ok:
            body.update(
                f"[yellow]![/] dr is signed in to a different DataRobot environment.\n\n"
                f"You selected [bold]{env_name}[/] — {endpoint}\n\n"
                f"Press [bold]a[/] to run [bold]dr auth login {endpoint}[/].\n"
                "Press [bold]r[/] to re-check after logging in."
            )
        else:
            body.update(
                f"[yellow]✗[/] Not authenticated with DataRobot ({env_name}).\n\n"
                f"Press [bold]a[/] to run [bold]dr auth login {endpoint}[/] in this terminal.\n"
                "Press [bold]r[/] to re-check after logging in."
            )

    def _render_prereq_table(self) -> None:
        table = self.query_one("#prereq-table", DataTable)
        table.display = True
        table.clear(columns=True)
        table.add_columns("Tool", "Status", "Install hint")
        if not self._check_result:
            return
        for prereq in self._check_result.prerequisites:
            status = "[green]installed[/]" if prereq.installed else "[red]missing[/]"
            table.add_row(prereq.name, status, prereq.install_hint if not prereq.installed else "")

        body = self.query_one("#setup-detail-body", Static)
        if self._check_result.prerequisites_ok:
            body.update("[green]All prerequisites installed.[/] Press [enter] to continue.")
        else:
            body.update("[yellow]Install missing tools, then press [r] to recheck.[/]")

    def on_key(self, event: events.Key) -> None:
        """Ensure Enter advances the wizard (bindings alone miss some terminals)."""
        if event.key not in ("enter", "ctrl+m"):
            return
        focused = self.screen.focused
        if isinstance(focused, Input):
            return
        if isinstance(focused, RadioSet):
            return
        self.action_continue_step()
        event.prevent_default()
        event.stop()

    def action_continue_step(self) -> None:
        if self._step == 0:
            self._finish_step(0)
            self._show_step(1)
        elif self._step == 1:
            if self._check_result and self._check_result.prerequisites_ok:
                self._finish_step(1)
                self._show_step(2)
            else:
                self.run_prereq_check()
        elif self._step == STEP_ENVIRONMENT:
            if self._save_credentials():
                self._finish_step(STEP_ENVIRONMENT)
                self._show_step(STEP_AUTH)
        elif self._step == STEP_AUTH:
            if self._auth_ready():
                self._finish_step(STEP_AUTH)
                self._show_step(STEP_VERIFY)
            else:
                self.run_auth_check()
        elif self._step == STEP_COMPLETE:
            self.exit()

    def action_recheck(self) -> None:
        if self._step == STEP_TOOLS:
            self.run_prereq_check()
        elif self._step == STEP_AUTH:
            self.run_auth_check()

    def action_auth_login(self) -> None:
        if self._step == STEP_AUTH:
            self.run_auth_login()

    def _finish_step(self, step: int) -> None:
        panel = self.query_one(SetupStepPanel)
        panel.set_step_status(step, "done")

    def _resolve_endpoint(self) -> str:
        if self.query_one("#ep-staging", RadioButton).value:
            return ENDPOINT_PRESETS["staging"]
        if self.query_one("#ep-custom", RadioButton).value:
            return normalize_endpoint(self.query_one("#endpoint-custom", Input).value)
        return ENDPOINT_PRESETS["production"]

    def _save_credentials(self) -> bool:
        endpoint = self._resolve_endpoint()
        token = self.query_one("#token-field", Input).value.strip()
        model = self.query_one("#model-field", Input).value.strip() or DEFAULT_MODEL

        if not endpoint:
            self.notify("Endpoint URL is required", severity="error")
            return False
        if not token:
            self.notify("API token is required", severity="error")
            return False

        write_env_file(
            {
                # dr CLI and DR SDK expect the /api/v2 form in the environment
                "DATAROBOT_ENDPOINT": api_endpoint(endpoint),
                "DATAROBOT_API_TOKEN": token,
                "SUPERROBOT_MODEL": model,
            }
        )
        return True

    @work(exclusive=True)
    async def run_prereq_check(self) -> None:
        self._check_result = await run_all_checks()
        self._render_prereq_table()

    @work(exclusive=True)
    async def run_auth_check(self) -> None:
        body = self.query_one("#setup-detail-body", Static)
        body.update("[dim]Re-checking dr auth…[/]")
        auth_ok = await check_auth()
        if self._check_result:
            self._check_result = replace(self._check_result, auth_ok=auth_ok)
        else:
            self._check_result = SetupCheckResult(auth_ok=auth_ok)
        self._render_auth_step(self.query_one("#setup-detail-body", Static))

    @work(exclusive=True)
    async def run_auth_login(self) -> None:
        body = self.query_one("#setup-detail-body", Static)
        endpoint = self._selected_endpoint()
        body.update(f"[dim]Running dr auth login {endpoint}…[/]")
        try:
            with self.suspend():
                ok = await SetupRunner()._run_auth_login(endpoint)
        except SuspendNotSupported:
            # e.g. headless test driver — run without releasing the terminal
            ok = await SetupRunner()._run_auth_login(endpoint)
        if self._check_result:
            self._check_result = replace(self._check_result, auth_ok=ok)
        else:
            self._check_result = SetupCheckResult(auth_ok=ok)
        self._render_auth_step(body)

    @work(exclusive=True)
    async def run_gateway_check(self) -> None:
        if self._gateway_running:
            return
        self._gateway_running = True
        panel = self.query_one(SetupStepPanel)
        body = self.query_one("#setup-detail-body", Static)

        ok, error = await check_gateway()
        self._gateway_running = False

        if ok:
            endpoint = os.environ.get("DATAROBOT_ENDPOINT", "")
            model = os.environ.get("SUPERROBOT_MODEL", DEFAULT_MODEL)
            mark_setup_complete(endpoint, model=model)
            panel.set_step_status(4, "done")
            panel.current_step = 5
            panel.set_step_status(5, "active")
            self._step = 5
            self.query_one("#setup-detail-title", Static).update("Complete")
            body.update(
                "[green]✓ LLM Gateway reachable[/]\n\n"
                "[bold green]Setup complete.[/]\n\n"
                "  superrobot import <path>   — brownfield migrate\n"
                "  superrobot new             — greenfield wizard\n"
                "  superrobot template        — DR template browser"
            )
            self.query_one("#setup-hints", Static).update(HINTS[5])
        else:
            panel.fail_current()
            self._show_step(STEP_ENVIRONMENT)
            body.update(
                f"[red]✗ Gateway check failed:[/] {error}\n\n"
                "[yellow]Fix credentials below, then press [enter] to retry.[/]"
            )
            self.notify("Gateway check failed — update credentials", severity="error")
