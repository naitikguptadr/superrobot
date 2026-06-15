"""Setup TUI integration tests."""

from __future__ import annotations

import asyncio

import pytest

from superrobot.tui.setup_app import STEP_AUTH, STEP_ENVIRONMENT, SetupApp


def test_setup_welcome_advances_on_enter() -> None:
    async def run() -> None:
        app = SetupApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            panel = app.query_one("#setup-step-panel")
            assert panel.size.height > 5, f"step panel too short: {panel.size}"
            assert "Welcome" in str(panel.render())
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert app._step == 1
            title = app.query_one("#setup-detail-title")
            assert "Prerequisites" in str(title.render())

    asyncio.run(run())


def test_environment_step_offers_staging_and_precedes_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATAROBOT_ENDPOINT", raising=False)
    monkeypatch.delenv("DATAROBOT_API_TOKEN", raising=False)

    async def run() -> None:
        app = SetupApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app._show_step(STEP_ENVIRONMENT)
            await pilot.pause(0.1)
            staging = app.query_one("#ep-staging")
            assert staging.display
            assert "staging.datarobot.com" in str(staging.label)
            assert STEP_ENVIRONMENT < STEP_AUTH

    asyncio.run(run())


def test_recheck_key_works_after_environment_form_had_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a hidden-but-focused Input swallowed the r/a key bindings."""
    monkeypatch.setenv("DATAROBOT_ENDPOINT", "https://staging.datarobot.com")
    calls: list[bool] = []

    async def fake_check_auth() -> bool:
        calls.append(True)
        return True

    import superrobot.tui.setup_app as setup_app_module

    monkeypatch.setattr(setup_app_module, "check_auth", fake_check_auth)
    monkeypatch.setattr(setup_app_module, "auth_matches_endpoint", lambda _url: True)

    async def run() -> None:
        app = SetupApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app._show_step(STEP_ENVIRONMENT)
            await pilot.pause(0.1)
            app.query_one("#token-field").focus()
            await pilot.pause(0.1)
            app._show_step(STEP_AUTH)
            await pilot.pause(0.1)
            await pilot.press("r")
            await pilot.pause(0.3)
            assert calls, "pressing r on the Auth step did not trigger an auth re-check"
            body = str(app.query_one("#setup-detail-body").render())
            assert "dr auth check passed" in body

    asyncio.run(run())


def test_auth_step_targets_selected_staging_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATAROBOT_ENDPOINT", "https://staging.datarobot.com")

    async def run() -> None:
        app = SetupApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app._show_step(STEP_AUTH)
            await pilot.pause(0.1)
            body = str(app.query_one("#setup-detail-body").render())
            assert "dr auth login https://staging.datarobot.com" in body

    asyncio.run(run())
