"""Root Textual application shell."""

from __future__ import annotations

import os
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header

from daylight import themes
from daylight.components.modules.file_list import FilesPage
from daylight.managers import ConfigManager, FileManager
from daylight.models import AppConfig

__all__ = ["DaylightApp"]


class DaylightApp(App[None]):
    """Daylight Commander application."""

    TITLE = "Daylight Commander"
    CSS_PATH = ["styles/index.tcss", "styles/files.tcss", "styles/operations.tcss"]
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
    ]
    PAGES = {"files": FilesPage}

    def __init__(
        self,
        config: AppConfig | None = None,
        config_path: str | os.PathLike[str] | None = None,
    ) -> None:
        # A config passed in wins; otherwise the persisted one is loaded, so a
        # preference saved by a previous run (the hidden-files toggle, the theme)
        # is restored on restart. A missing or broken file falls back to the
        # defaults inside ``ConfigManager.load``.
        self.config_manager = ConfigManager(config_path)
        self.app_config: AppConfig = config or self.config_manager.load()
        self.file_manager = FileManager(show_hidden=self.app_config.show_hidden)
        self.start_path: Path = self.app_config.start_path or Path.cwd()
        super().__init__()

    def on_mount(self) -> None:
        """Activate the configured Daylight theme."""
        theme = themes.get_theme(self.app_config.theme)
        self.register_theme(themes.to_textual_theme(theme))
        self.theme = theme.name

    def compose(self) -> ComposeResult:
        """Compose the shell: header, the Files page and the footer."""
        yield Header()
        yield FilesPage(
            self.start_path,
            file_manager=self.file_manager,
            config_manager=self.config_manager,
        )
        yield Footer()
