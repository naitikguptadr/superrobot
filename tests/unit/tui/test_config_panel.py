"""Config panel tests."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.widgets import TextArea

from superrobot.tui.config_panel import CONFIG_TABS, ConfigPanel, _tab_slug


class _Host(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.saved: dict[str, str] | None = None

    def compose(self) -> ComposeResult:
        yield ConfigPanel(id="config-panel")

    def on_config_panel_saved(self, message: ConfigPanel.Saved) -> None:
        self.saved = message.files


def test_tab_slug_strips_invalid_id_chars() -> None:
    # Regression: 'tab-workflow.yaml' crashed the app (dots invalid in Textual ids)
    assert _tab_slug("workflow.yaml") == "workflow-yaml"
    assert _tab_slug(".env.template") == "env-template"


def test_config_panel_loads_files_into_editable_areas() -> None:
    async def run() -> None:
        app = _Host()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            panel = app.query_one(ConfigPanel)
            panel.set_files(
                {
                    "agent/agent/workflow.yaml": "model: gpt",
                    "agent/agent/myagent.py": "class MyAgent: ...",
                    "pyproject.toml": "[project]",
                    ".env.template": "DATAROBOT_API_TOKEN=",
                }
            )
            await pilot.pause(0.1)
            editor = app.query_one("#content-workflow-yaml", TextArea)
            assert editor.text == "model: gpt"
            assert not editor.read_only
            # component.tsx has no content -> stays read-only placeholder
            tsx = app.query_one("#content-component-tsx", TextArea)
            assert tsx.read_only

    asyncio.run(run())


def test_config_panel_save_posts_edited_files() -> None:
    async def run() -> None:
        app = _Host()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            panel = app.query_one(ConfigPanel)
            panel.set_files({"agent/agent/workflow.yaml": "model: gpt"})
            await pilot.pause(0.1)
            editor = app.query_one("#content-workflow-yaml", TextArea)
            editor.load_text("model: edited")
            panel.action_save()
            await pilot.pause(0.1)
            assert app.saved is not None
            assert app.saved["agent/agent/workflow.yaml"] == "model: edited"

    asyncio.run(run())


def test_all_config_tabs_produce_valid_ids() -> None:
    import re

    for tab in CONFIG_TABS:
        slug = _tab_slug(tab)
        assert re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]*", slug), slug
