"""Config edit modal."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea


class ConfigEditModal(ModalScreen[str | None]):
    """Modal for editing a config file."""

    DEFAULT_CSS = """
    ConfigEditModal {
        align: center middle;
    }
    #config-edit-dialog {
        width: 80;
        height: 30;
        border: thick $success;
        background: $surface;
        padding: 1;
    }
    """

    def __init__(self, filename: str, content: str) -> None:
        super().__init__()
        self._filename = filename
        self._content = content

    def compose(self) -> ComposeResult:
        with Static(id="config-edit-dialog"):
            yield Static(f"[b]Edit {self._filename}[/b]")
            yield TextArea(self._content, id="config-editor", language="python")
            yield Button("Save", variant="primary", id="save-btn")
            yield Button("Cancel", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            content = self.query_one("#config-editor", TextArea).text
            self.dismiss(content)
        elif event.button.id == "cancel-btn":
            self.dismiss(None)
