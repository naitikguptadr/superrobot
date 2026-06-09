"""Greenfield wizard modal — 3 questions per AGENTS.md."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, RadioButton, RadioSet, Static

DR_SKILLS = ["web_search", "code_execution", "mcp_tools", "rag", "memory"]
DR_FRAMEWORKS = ["langgraph", "crewai", "llamaindex", "nat", "pydantic_ai"]


@dataclass
class GreenfieldSpec:
    """User answers from greenfield wizard."""

    agent_purpose: str
    tools: list[str]
    framework: str


class GreenfieldWizard(ModalScreen[GreenfieldSpec | None]):
    """Three-question greenfield wizard."""

    DEFAULT_CSS = """
    GreenfieldWizard {
        align: center middle;
    }
    #wizard-dialog {
        width: 70;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard-dialog"):
            yield Static("[b]Greenfield Agent Wizard[/b]")
            yield Label("What does your agent do?")
            yield Input(placeholder="e.g. Research assistant that searches the web", id="purpose")
            yield Label("Which tools does it need? (toggle)")
            with Vertical(id="skills"):
                for skill in DR_SKILLS:
                    yield Checkbox(skill, id=f"skill-{skill}")
            yield Label("Framework:")
            with RadioSet(id="framework-set"):
                for fw in DR_FRAMEWORKS:
                    yield RadioButton(fw, id=f"fw-{fw}")
            yield Button("Continue", variant="primary", id="continue-btn")
            yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        framework_set = self.query_one("#framework-set", RadioSet)
        first = framework_set.children[0]
        if isinstance(first, RadioButton):
            first.value = True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
            return
        purpose = self.query_one("#purpose", Input).value.strip()
        if not purpose:
            return
        tools = [skill for skill in DR_SKILLS if self.query_one(f"#skill-{skill}", Checkbox).value]
        framework_set = self.query_one("#framework-set", RadioSet)
        framework = DR_FRAMEWORKS[framework_set.pressed_index or 0]
        self.dismiss(GreenfieldSpec(agent_purpose=purpose, tools=tools, framework=framework))
