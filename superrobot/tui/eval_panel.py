"""Eval results DataTable widget."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from superrobot.models.eval_result import EvalSummary


class EvalPanel(Static):
    """Pre-deploy eval results table."""

    def compose(self) -> ComposeResult:
        yield Static("[b]EVALUATION[/b]")
        yield DataTable(id="eval-table")

    def on_mount(self) -> None:
        table = self.query_one("#eval-table", DataTable)
        table.add_columns("Run", "Status", "Latency", "Reason")

    def set_results(self, summary: EvalSummary) -> None:
        table = self.query_one("#eval-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Run", "Status", "Latency", "Reason")
        for result in summary.results:
            table.add_row(
                str(result.run_id),
                result.status,
                f"{result.latency_ms:.0f}ms",
                result.failure_reason or "",
            )
