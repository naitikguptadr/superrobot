"""README badge injection confirmation modal."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ReadmeBadgeModal(ModalScreen[bool]):
    """Show README diff and ask for confirmation."""

    DEFAULT_CSS = """
    ReadmeBadgeModal {
        align: center middle;
    }
    #badge-dialog {
        width: 80;
        height: 30;
        border: thick $success;
        background: $surface;
        padding: 1;
    }
    #badge-preview {
        height: 1fr;
        overflow-y: auto;
    }
    """

    def __init__(self, diff_preview: str) -> None:
        super().__init__()
        self._preview = diff_preview[:3000]

    def compose(self) -> ComposeResult:
        yield Static("[b]Add Deploy Badge to README?[/b]")
        yield Static(self._preview, id="badge-preview")
        yield Static("[y] commit badge  [n] skip")
        yield Button("Yes — write README", variant="primary", id="yes-btn")
        yield Button("Skip", id="no-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes-btn")
