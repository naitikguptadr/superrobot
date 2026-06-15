"""Config preview tabs widget — editable in place."""

from __future__ import annotations

import re

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static, TabbedContent, TabPane, TextArea

CONFIG_TABS = ["workflow.yaml", "myagent.py", "pyproject.toml", ".env.template", "component.tsx"]

_LANGUAGES = {
    "workflow.yaml": "yaml",
    "myagent.py": "python",
    "pyproject.toml": "toml",
    ".env.template": "bash",
    "component.tsx": "javascript",
}

_BUNDLE_PATHS = {
    "workflow.yaml": "agent/agent/workflow.yaml",
    "myagent.py": "agent/agent/myagent.py",
    "pyproject.toml": "pyproject.toml",
    ".env.template": ".env.template",
    "component.tsx": "ui/component.tsx",
}


def _tab_slug(tab: str) -> str:
    """Filename → valid Textual id (no dots, no leading punctuation)."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", tab).strip("-")


def _make_editor(tab: str) -> TextArea:
    editor = TextArea(
        f"# {tab} appears here after the Generate step\n",
        id=f"content-{_tab_slug(tab)}",
        read_only=True,
        show_line_numbers=True,
    )
    try:
        editor.language = _LANGUAGES.get(tab)
    except Exception:
        editor.language = None  # syntax extras unavailable for this language
    return editor


class ConfigPanel(Static):
    """Live, editable config preview with tabs. Ctrl+S saves to disk."""

    BINDINGS = [Binding("ctrl+s", "save", "Save config")]

    class Saved(Message):
        """Posted when the user saves edits; maps bundle path → content."""

        def __init__(self, files: dict[str, str]) -> None:
            self.files = files
            super().__init__()

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._files: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with TabbedContent():
            for tab in CONFIG_TABS:
                with TabPane(tab, id=f"tab-{_tab_slug(tab)}"):
                    yield _make_editor(tab)

    def on_mount(self) -> None:
        self.border_title = "CONFIG — editable, ctrl+s saves"

    def set_files(self, files: dict[str, str]) -> None:
        """Update displayed config files."""
        mapping = {tab: files.get(path, "") for tab, path in _BUNDLE_PATHS.items()}
        self._files = mapping
        for tab, content in mapping.items():
            editor = self.query_one(f"#content-{_tab_slug(tab)}", TextArea)
            if content:
                if editor.text != content:
                    editor.load_text(content)
                editor.read_only = False
            else:
                editor.load_text(f"# No {tab} generated yet\n")
                editor.read_only = True

    def focus_active_editor(self) -> None:
        """Put the cursor in the currently visible tab's editor."""
        tabs = self.query_one(TabbedContent)
        pane = tabs.get_pane(tabs.active) if tabs.active else None
        if pane is not None:
            pane.query_one(TextArea).focus()

    def action_save(self) -> None:
        edited: dict[str, str] = {}
        for tab, path in _BUNDLE_PATHS.items():
            editor = self.query_one(f"#content-{_tab_slug(tab)}", TextArea)
            if not editor.read_only and self._files.get(tab, "") != "":
                edited[path] = editor.text
        if edited:
            self.post_message(self.Saved(edited))
