"""Node click detail modal."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class NodeDetailModal(ModalScreen[None]):
    """Shows schema, latency, and cost estimates for a graph node."""

    DEFAULT_CSS = """
    NodeDetailModal {
        align: center middle;
    }
    #node-detail-dialog {
        width: 50;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    def __init__(self, node_id: str, label: str, node_type: str) -> None:
        super().__init__()
        self._node_id = node_id
        self._label = label
        self._node_type = node_type

    def compose(self) -> ComposeResult:
        with Static(id="node-detail-dialog"):
            yield Static(f"[b]{self._label}[/b]")
            yield Static(f"ID: {self._node_id}")
            yield Static(f"Type: {self._node_type}")
            yield Static("Est. latency: ~200ms")
            yield Static("Est. cost: ~$0.001")
            yield Button("Close", id="close-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.dismiss(None)
