"""DR template browser modal."""

from __future__ import annotations

import re
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static


@dataclass
class DrTemplate:
    """Parsed DR template entry."""

    name: str
    description: str
    framework: str = ""


def parse_templates_list(stdout: str) -> list[DrTemplate]:
    """Parse dr templates list output into structured entries."""
    templates: list[DrTemplate] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("-") and "name" in stripped.lower():
            continue
        # Try tab or multi-space separated: name  description  framework
        parts = re.split(r"\s{2,}|\t", stripped)
        if len(parts) >= 1 and parts[0]:
            name = parts[0].strip()
            if name.lower() in ("name", "template", "templates"):
                continue
            desc = parts[1].strip() if len(parts) > 1 else ""
            fw = parts[2].strip() if len(parts) > 2 else ""
            templates.append(DrTemplate(name=name, description=desc, framework=fw))
    if not templates and stdout.strip():
        # Fallback: one template per non-empty line
        for line in stdout.splitlines():
            name = line.strip()
            if name:
                templates.append(DrTemplate(name=name, description=""))
    return templates


class TemplateBrowser(ModalScreen[DrTemplate | None]):
    """Browsable table of DR templates."""

    DEFAULT_CSS = """
    TemplateBrowser {
        align: center middle;
    }
    #template-dialog {
        width: 80;
        height: 30;
        border: thick $accent;
        background: $surface;
        padding: 1;
    }
    """

    def __init__(self, templates: list[DrTemplate], **kwargs: object) -> None:
        super().__init__()
        self._templates = templates

    def compose(self) -> ComposeResult:
        yield Static("[b]Select DR Template[/b]", id="template-header")
        yield DataTable(id="template-table")
        yield Button("Clone selected", variant="primary", id="clone-btn")
        yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        table = self.query_one("#template-table", DataTable)
        table.add_columns("Name", "Description", "Framework")
        for tmpl in self._templates:
            table.add_row(tmpl.name, tmpl.description, tmpl.framework)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
            return
        table = self.query_one("#template-table", DataTable)
        if table.cursor_row is None:
            return
        row = table.cursor_row
        if 0 <= row < len(self._templates):
            self.dismiss(self._templates[row])
