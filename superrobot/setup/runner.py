"""Interactive and non-interactive setup runner."""

from __future__ import annotations

import os
from pathlib import Path

from rich.console import Console

from superrobot.setup.config import save_state, write_token_env
from superrobot.setup.doctor import run_doctor
from superrobot.setup.endpoints import ENDPOINT_PRESETS, api_endpoint, normalize_endpoint
from superrobot.setup.gateway import verify_gateway
from superrobot.setup.models import AuthMethod, DoctorResult, SetupState
from superrobot.setup.probes import check_dr_auth, probe_capabilities


async def run_setup(
    *,
    console: Console | None = None,
    config_root: str | Path | None = None,
    endpoint: str | None = None,
    token: str | None = None,
    model: str | None = None,
    skip_gateway: bool = False,
    non_interactive: bool = False,
) -> DoctorResult:
    """Configure endpoint + auth, verify Gateway, persist state."""
    out = console or Console()
    selected_endpoint = endpoint or os.environ.get("DATAROBOT_ENDPOINT", "")
    selected_token = token or os.environ.get("DATAROBOT_API_TOKEN", "")
    selected_model = model or os.environ.get("SUPERROBOT_MODEL", "azure/gpt-5-5-2026-04-23")

    if not non_interactive and not selected_endpoint:
        out.print("[bold]SuperRobot setup[/] — DataRobot environment")
        out.print("1) production  2) staging  3) custom")
        choice = input("Choose [1]: ").strip() or "1"
        if choice == "2":
            selected_endpoint = ENDPOINT_PRESETS["staging"]
        elif choice == "3":
            selected_endpoint = input("Platform URL: ").strip()
        else:
            selected_endpoint = ENDPOINT_PRESETS["production"]

    if not selected_endpoint:
        out.print("[red]Endpoint required[/]")
        return DoctorResult(ready=False, checks=(("endpoint", False, "missing"),))

    normalized = normalize_endpoint(selected_endpoint)
    api = api_endpoint(normalized)
    out.print(f"[dim]Endpoint[/] {api}")

    auth = await check_dr_auth()
    auth_method = AuthMethod.NONE
    if auth.ok:
        auth_method = AuthMethod.DR_CLI
        out.print("[green]✓[/] dr auth check passed")
    else:
        out.print(f"[yellow]dr auth[/] {auth.detail}")
        if not selected_token and not non_interactive:
            selected_token = input(
                "Paste DATAROBOT_API_TOKEN (or leave blank to run dr auth login): "
            ).strip()
        if selected_token:
            auth_method = AuthMethod.API_TOKEN
        elif not non_interactive:
            out.print("Run: [cyan]dr auth login[/] " + normalized)
            return DoctorResult(ready=False, checks=(("auth", False, "login required"),))

    if not selected_token and auth_method is AuthMethod.DR_CLI:
        # Token still useful for Gateway HTTP probes; allow env-only
        selected_token = os.environ.get("DATAROBOT_API_TOKEN", "")

    if not selected_token:
        out.print("[red]API token required for Gateway verification[/]")
        return DoctorResult(ready=False, checks=(("auth", False, "token required"),))

    if not skip_gateway:
        await verify_gateway(normalized, selected_token, model=selected_model)
        out.print("[green]✓[/] LLM Gateway verified")

    capabilities = await probe_capabilities(normalized, selected_token)
    state = SetupState(
        endpoint=normalized,
        auth_method=auth_method,
        capabilities=capabilities,
        model=selected_model,
    )
    save_state(state, config_root)
    write_token_env(
        endpoint=api,
        token=selected_token,
        model=selected_model,
        root=config_root,
    )
    os.environ["DATAROBOT_ENDPOINT"] = api
    os.environ["DATAROBOT_API_TOKEN"] = selected_token
    os.environ["SUPERROBOT_MODEL"] = selected_model
    out.print("[green]✓[/] Setup saved")
    root = str(config_root) if config_root else None
    return await run_doctor(config_root=root, skip_gateway=True)
