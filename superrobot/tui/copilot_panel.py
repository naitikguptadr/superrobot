"""Streaming AI copilot RichLog widget."""

from __future__ import annotations

from rich.markup import escape
from rich.rule import Rule
from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import RichLog, Static

# suggestions SuperRobot can actually auto-apply (mirrors config_generator.apply_fix)
_APPLIABLE_MARKERS = ("flat import", "drum")


class CopilotPanel(Static):
    """AI Copilot panel with streaming log output."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._stream_buffer: str = ""

    def compose(self) -> ComposeResult:
        # min_width: RichLog defaults to 78, which forces horizontal overflow
        # in the ~38-col copilot column
        yield RichLog(id="copilot-log", wrap=True, min_width=10, highlight=False, markup=True)
        yield Static("", id="copilot-stream")

    def on_mount(self) -> None:
        self.border_title = "AI COPILOT"

    def log_message(self, message: str) -> None:
        log = self.query_one("#copilot-log", RichLog)
        try:
            log.write(message)
        except Exception:
            # message contained Rich-markup-breaking brackets (LLM text, stderr)
            log.write(Text(message))

    def log_stage(self, stage: str) -> None:
        """Visual separator marking which pipeline stage the next entry covers."""
        self.query_one("#copilot-log", RichLog).write(Rule(stage, style="dim"))

    def begin_stream(self, header: str) -> None:
        """Start a streamed copilot response shown live below the log."""
        self._stream_buffer = ""
        self.query_one("#copilot-stream", Static).update(f"[dim]{header}[/]")

    def log_stream_chunk(self, chunk: str) -> None:
        """Accumulate a stream chunk and re-render the in-progress response."""
        self._stream_buffer += chunk
        self.query_one("#copilot-stream", Static).update(escape(self._stream_buffer))

    def end_stream(self) -> None:
        """Commit the finished response to the log and clear the live area."""
        if self._stream_buffer:
            log = self.query_one("#copilot-log", RichLog)
            fix_count = 0
            for raw_line in self._stream_buffer.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("[FIX]"):
                    fix_count += 1
                    body = line.removeprefix("[FIX]").lstrip(": ").strip()
                    entry = Text()
                    entry.append(f"  {fix_count} ", style="bold black on yellow")
                    entry.append(" ")
                    entry.append(body)
                    log.write(entry)
                    log.write(Text(""))
                else:
                    log.write(Text(line, style="default"))
            if fix_count:
                lowered = self._stream_buffer.lower()
                if any(marker in lowered for marker in _APPLIABLE_MARKERS):
                    log.write(Text("→ press a to apply this config fix", style="bold green"))
                else:
                    log.write(
                        Text(
                            "ⓘ suggestions for your source repo — apply in your own code",
                            style="dim italic",
                        )
                    )
        self._stream_buffer = ""
        self.query_one("#copilot-stream", Static).update("")

    @property
    def last_fix_is_appliable(self) -> bool:
        """Whether the most recent suggestion maps to an automatic config fix."""
        return any(marker in self._stream_buffer.lower() for marker in _APPLIABLE_MARKERS)

    def clear(self) -> None:
        self.query_one("#copilot-log", RichLog).clear()
