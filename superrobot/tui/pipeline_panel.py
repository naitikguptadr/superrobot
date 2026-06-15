"""Pipeline step tracker widget."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import Static

PIPELINE_STEPS = ["Scan", "Analyze", "Generate", "Build UI", "Evaluate", "Deploy"]

STEP_DESCRIPTIONS = [
    "detect framework & entry points",
    "map to a DR framework (LLM)",
    "write DR config files",
    "generate dr-ui component",
    "5-shot pre-deploy eval",
    "pulumi deploy via dr",
]

STEP_ICONS = {
    "pending": "○",
    "active": "●",
    "done": "✓",
    "failed": "✗",
}

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class PipelinePanel(Static):
    """Step tracker with status icons, notes, and a clear active row."""

    current_step: reactive[int] = reactive(0)
    step_statuses: reactive[dict[int, str]] = reactive({})
    working: reactive[bool] = reactive(False)
    """True while a stage is running — the active step animates a spinner."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._notes: dict[int, str] = {}
        self._frame = 0

    def compose(self) -> ComposeResult:
        yield Static(id="pipeline-content")

    def on_mount(self) -> None:
        self.border_title = "PIPELINE"
        self.set_interval(0.12, self._tick)
        self._refresh_display()

    def _tick(self) -> None:
        if self.working:
            self._frame = (self._frame + 1) % len(SPINNER_FRAMES)
            self._refresh_display()

    def watch_working(self, _value: bool) -> None:
        self._refresh_display()

    def watch_current_step(self, _value: int) -> None:
        self._refresh_display()

    def watch_step_statuses(self, _value: dict[int, str]) -> None:
        self._refresh_display()

    def set_step_status(self, step: int, status: str) -> None:
        statuses = dict(self.step_statuses)
        statuses[step] = status
        self.step_statuses = statuses

    def set_step_note(self, step: int, note: str) -> None:
        """Attach a short result note shown next to a step (e.g. 'langchain 0.80')."""
        self._notes[step] = note
        self._refresh_display()

    def advance(self) -> None:
        if self.current_step < len(PIPELINE_STEPS) - 1:
            self.set_step_status(self.current_step, "done")
            self.current_step += 1
            self.set_step_status(self.current_step, "active")

    def complete_step(self, step: int) -> None:
        """Mark `step` and everything before it done, activate the next step.

        Unlike advance(), this is correct even when `step` is ahead of
        current_step (e.g. Generate completes while current_step is Analyze).
        """
        statuses = dict(self.step_statuses)
        for i in range(step + 1):
            statuses[i] = "done"
        next_step = min(step + 1, len(PIPELINE_STEPS) - 1)
        if next_step > step:
            statuses[next_step] = "active"
        self.step_statuses = statuses
        self.current_step = next_step

    def fail_current(self) -> None:
        self.set_step_status(self.current_step, "failed")

    def _refresh_display(self) -> None:
        if not self.is_mounted:
            return
        # truncate notes so step rows never wrap in narrow terminals
        detail_width = max(self.size.width - 18, 10)
        lines: list[str] = []
        for i, name in enumerate(PIPELINE_STEPS):
            status = self.step_statuses.get(i, "pending")
            if i == self.current_step and status == "pending":
                status = "active"
            icon = STEP_ICONS.get(status, "○")
            note = self._notes.get(i, "")
            is_current = i == self.current_step and status not in ("done", "failed")

            detail = note or (STEP_DESCRIPTIONS[i] if is_current else "")
            if len(detail) > detail_width:
                detail = detail[: detail_width - 1] + "…"
            note = note if len(note) <= detail_width else note[: detail_width - 1] + "…"
            name_part = f"{name:<9}"
            if status == "done":
                line = f"  [$success]{icon} {name_part}[/]"
                if note:
                    line += f" [dim]{note}[/]"
            elif status == "failed":
                line = f"  [$error]{icon} {name_part}[/]"
                if note:
                    line += f" [$error]{note}[/]"
            elif is_current:
                # spinner while a stage runs; ▶ when waiting for the user
                marker = SPINNER_FRAMES[self._frame] if self.working else "▶"
                line = f"[bold $accent]{marker} {icon} {name_part}[/]"
                if detail:
                    line += f" [dim $accent]{detail}[/]"
            else:
                line = f"  [$text-muted]{icon} {name_part}[/]"
            lines.append(line)
        self.query_one("#pipeline-content", Static).update("\n".join(lines))
