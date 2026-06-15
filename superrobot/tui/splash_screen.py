"""Launch splash — DataRobot mark while the pipeline warms up."""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Center, Middle
from textual.screen import Screen
from textual.widgets import Static

LOGO = r"""
             ▄█▄
             ███
    ▄▄▄▄▄▄▄▄▄███▄▄▄▄▄▄▄▄▄
   ██                   ██
   ██    ███     ███    ██
█████    ███     ███    █████
█████                   █████
   ██   ▄▄▄▄▄▄▄▄▄▄▄▄▄   ██
   ██   ▀▀▀▀▀▀▀▀▀▀▀▀▀   ██
    ▀█▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄█▀
"""

WORDMARK = """[b]S U P E R R O B O T[/b]
[dim]bring any agent to[/] [b $accent]DataRobot[/]"""

_PULSE = ("●○○", "○●○", "○○●", "○●○")


class SplashScreen(Screen[None]):
    """Full-screen splash shown while the import pipeline spins up."""

    DEFAULT_CSS = """
    SplashScreen {
        align: center middle;
        background: $surface;
    }
    #splash-logo {
        color: $accent;
        text-align: center;
        width: auto;
    }
    #splash-wordmark {
        text-align: center;
        width: auto;
        margin-top: 1;
    }
    #splash-pulse {
        color: $accent;
        text-align: center;
        width: auto;
        margin-top: 1;
    }
    #splash-meta {
        text-align: center;
        width: auto;
        margin-top: 2;
    }
    """

    def __init__(self, repo_label: str = "") -> None:
        super().__init__()
        self._repo_label = repo_label
        self._frame = 0

    def compose(self) -> ComposeResult:
        import os

        from superrobot import __version__
        from superrobot.setup.constants import endpoint_label

        env_name = endpoint_label(os.environ.get("DATAROBOT_ENDPOINT", ""))
        with Middle():
            with Center():
                yield Static(LOGO, id="splash-logo")
            with Center():
                yield Static(WORDMARK, id="splash-wordmark")
            with Center():
                yield Static("", id="splash-pulse")
            with Center():
                yield Static(
                    f"[dim]v{__version__} · {env_name} · any key to skip[/]",
                    id="splash-meta",
                )

    def on_mount(self) -> None:
        self.set_interval(0.18, self._tick)
        self._tick()

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(_PULSE)
        label = f"scanning {self._repo_label}" if self._repo_label else "warming up"
        self.query_one("#splash-pulse", Static).update(f"{_PULSE[self._frame]}  [dim]{label}[/]")

    def on_key(self, _event: events.Key) -> None:
        """Any key skips the splash."""
        if self.is_current:
            self.dismiss(None)
