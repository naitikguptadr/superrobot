"""Streaming AI copilot RichLog widget."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import RichLog, Static


class CopilotPanel(Static):
    """AI Copilot panel with streaming log output."""

    def compose(self) -> ComposeResult:
        yield Static("[b]AI COPILOT[/b]")
        yield RichLog(id="copilot-log", highlight=True, markup=True)

    def log_message(self, message: str) -> None:
        self.query_one("#copilot-log", RichLog).write(message)

    def log_stream_chunk(self, chunk: str) -> None:
        self.query_one("#copilot-log", RichLog).write(chunk, scroll_end=True)

    def clear(self) -> None:
        self.query_one("#copilot-log", RichLog).clear()
