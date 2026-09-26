"""Root Textual application shell."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static

from daylight import themes
from daylight.config import DEFAULT_CONFIG
from daylight.models import AppConfig

__all__ = ["DaylightApp"]


class DaylightApp(App[None]):
    """Daylight Commander application (minimal shell for DC-001)."""

    TITLE = "Daylight Commander"
    CSS_PATH = "styles/index.tcss"
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, config: AppConfig | None = None) -> None:
        self.app_config: AppConfig = config or DEFAULT_CONFIG
        super().__init__()

    def on_mount(self) -> None:
        """Activate the configured Daylight theme."""
        theme = themes.get_theme(self.app_config.theme)
        self.register_theme(themes.to_textual_theme(theme))
        self.theme = theme.name

    def compose(self) -> ComposeResult:
        """Compose the shell: header, status line, footer."""
        yield Header()
        yield Static("Daylight Commander", id="daylight-status")
        yield Footer()
