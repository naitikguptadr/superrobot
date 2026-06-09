"""dr-ui generation modal."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class UIBuilderModal(ModalScreen[str | None]):
    """Modal for describing a dr-ui component to generate."""

    DEFAULT_CSS = """
    UIBuilderModal {
        align: center middle;
    }
    #ui-builder-dialog {
        width: 60;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Static(id="ui-builder-dialog"):
            yield Static("[b]Generate dr-ui Component[/b]")
            yield Input(placeholder="Describe the component...", id="ui-description")
            yield Button("Generate", variant="primary", id="generate-btn")
            yield Button("Skip", id="skip-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "generate-btn":
            desc = self.query_one("#ui-description", Input).value
            self.dismiss(desc if desc else None)
        elif event.button.id == "skip-btn":
            self.dismiss(None)
