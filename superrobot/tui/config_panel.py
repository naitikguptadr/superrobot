"""Config preview tabs widget."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static, TabbedContent, TabPane

CONFIG_TABS = ["workflow.yaml", "myagent.py", "pyproject.toml", ".env.template"]


class ConfigPanel(Static):
    """Live config preview with tabs."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._files: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with TabbedContent():
            for tab in CONFIG_TABS:
                with TabPane(tab, id=f"tab-{tab}"):
                    yield Static(f"No {tab} generated yet", id=f"content-{tab}")

    def set_files(self, files: dict[str, str]) -> None:
        """Update displayed config files."""
        mapping = {
            "workflow.yaml": files.get("agent/agent/workflow.yaml", ""),
            "myagent.py": files.get("agent/agent/myagent.py", ""),
            "pyproject.toml": files.get("pyproject.toml", ""),
            ".env.template": files.get(".env.template", ""),
        }
        self._files = mapping
        for tab, content in mapping.items():
            widget = self.query_one(f"#content-{tab}", Static)
            if content:
                truncated = content[:2000]
                suffix = "..." if len(content) > 2000 else ""
                display = truncated + suffix
            else:
                display = f"No {tab}"
            widget.update(display)
