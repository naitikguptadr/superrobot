"""Eval results DataTable widget."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from superrobot.models.eval_result import EvalSummary

_STATUS_MARKUP = {
    "pass": "[green]✓ pass[/]",
    "fail": "[yellow]! fail[/]",
    "error": "[red]✗ error[/]",
}


class EvalPanel(Static):
    """Pre-deploy eval results table."""

    def compose(self) -> ComposeResult:
        yield Static("", id="eval-summary")
        yield DataTable(id="eval-table")

    def on_mount(self) -> None:
        self.border_title = "EVALUATION"
        table = self.query_one("#eval-table", DataTable)
        table.add_columns("Run", "Status", "Latency", "Reason")

    def set_results(self, summary: EvalSummary) -> None:
        self.display = True  # hidden via CSS until there are results to show
        colour = "green" if summary.passed == summary.total else "yellow"
        if summary.errors == summary.total:
            colour = "red"
        self.query_one("#eval-summary", Static).update(
            f"[bold {colour}]{summary.passed}/{summary.total} passed[/]"
            f"  [dim]· {summary.failed} failed · {summary.errors} errors"
            " · failures are warnings, not blockers[/]"
        )
        table = self.query_one("#eval-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Run", "Status", "Latency", "Reason")
        for result in summary.results:
            table.add_row(
                str(result.run_id),
                _STATUS_MARKUP.get(result.status, result.status),
                f"{result.latency_ms:.0f}ms",
                result.failure_reason or "",
            )
