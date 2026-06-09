"""Pipeline step tracker widget."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import Static

PIPELINE_STEPS = ["Scan", "Analyze", "Generate", "Build UI", "Evaluate", "Deploy"]

STEP_ICONS = {
    "pending": "○",
    "active": "●",
    "done": "✓",
    "failed": "✗",
}


class PipelinePanel(Static):
    """Step tracker with status icons."""

    current_step: reactive[int] = reactive(0)
    step_statuses: reactive[dict[int, str]] = reactive({})

    def compose(self) -> ComposeResult:
        yield Static(id="pipeline-content")

    def on_mount(self) -> None:
        self._refresh_display()

    def watch_current_step(self, _value: int) -> None:
        self._refresh_display()

    def watch_step_statuses(self, _value: dict[int, str]) -> None:
        self._refresh_display()

    def set_step_status(self, step: int, status: str) -> None:
        statuses = dict(self.step_statuses)
        statuses[step] = status
        self.step_statuses = statuses

    def advance(self) -> None:
        if self.current_step < len(PIPELINE_STEPS) - 1:
            self.set_step_status(self.current_step, "done")
            self.current_step += 1
            self.set_step_status(self.current_step, "active")

    def _refresh_display(self) -> None:
        lines: list[str] = ["[b]PIPELINE[/b]", ""]
        for i, name in enumerate(PIPELINE_STEPS):
            status = self.step_statuses.get(i, "pending")
            if i == self.current_step and status == "pending":
                status = "active"
            icon = STEP_ICONS.get(status, "○")
            css = f"pipeline-step-{status}"
            lines.append(f"[{css}]{icon}  {name}[/]")
        lines.extend(["", "[enter] continue  [e] edit  [r] re-analyze"])
        self.query_one("#pipeline-content", Static).update("\n".join(lines))
