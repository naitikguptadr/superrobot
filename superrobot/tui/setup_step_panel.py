"""Setup wizard step tracker — mirrors pipeline panel."""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static

from superrobot.setup.constants import SETUP_STEPS

STEP_ICONS = {
    "pending": "○",
    "active": "●",
    "done": "✓",
    "failed": "✗",
}


class SetupStepPanel(Static):
    """Left-rail setup step tracker."""

    current_step: reactive[int] = reactive(0)
    step_statuses: reactive[dict[int, str]] = reactive({})

    def on_mount(self) -> None:
        self.set_step_status(0, "active")
        self.call_after_refresh(self._refresh)

    def watch_current_step(self, _value: int) -> None:
        self._refresh()

    def watch_step_statuses(self, _value: dict[int, str]) -> None:
        self._refresh()

    def set_step_status(self, step: int, status: str) -> None:
        statuses = dict(self.step_statuses)
        statuses[step] = status
        self.step_statuses = statuses

    def advance(self) -> None:
        if self.current_step < len(SETUP_STEPS) - 1:
            self.set_step_status(self.current_step, "done")
            self.current_step += 1
            self.set_step_status(self.current_step, "active")

    def fail_current(self) -> None:
        self.set_step_status(self.current_step, "failed")

    def _refresh(self) -> None:
        if not self.is_mounted:
            return
        lines: list[str] = ["[b]SETUP[/b]", ""]
        for i, name in enumerate(SETUP_STEPS):
            status = self.step_statuses.get(i, "pending")
            if i == self.current_step and status == "pending":
                status = "active"
            icon = STEP_ICONS.get(status, "○")
            css = f"pipeline-step-{status}"
            lines.append(f"[{css}]{icon}  {name}[/]")
        self.update("\n".join(lines))
