"""The Settings tab: which theme is active, and which keys are bound (FR-601).

Deliberately just the two things the config is authoritative about -- the theme
and the keybindings. Everything else about the settings form is out of scope for
DC-005; the theme list is the one control here, and picking a theme is the same
:meth:`DaylightApp.set_theme` the command palette calls, so both routes persist
the choice the same way.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import OptionList, Static

from daylight import themes
from daylight.components.shell import format_key
from daylight.config import DEFAULT_KEYBINDINGS
from daylight.models import AppConfig

__all__ = ["SettingsPage"]

#: Width of the key column in the keybinding listing.
KEY_COLUMN = 12


class SettingsPage(Vertical):
    """Theme picker plus the effective keybindings."""

    DEFAULT_CLASSES = "settings-page"

    def __init__(self) -> None:
        self._theme_heading = Static("Theme", classes="settings-heading")
        self._themes = OptionList(*themes.theme_names(), id="settings-themes")
        self._key_heading = Static("Keybindings", classes="settings-heading")
        self._keys = Static("", id="settings-keys", classes="settings-keys")
        super().__init__(id="settings-page")

    # -- queries -----------------------------------------------------------

    @property
    def themes_list(self) -> OptionList:
        """The theme picker."""
        return self._themes

    @property
    def keys_widget(self) -> Static:
        """The keybinding listing."""
        return self._keys

    @property
    def app_config(self) -> AppConfig:
        """The running configuration: the keybinding overrides come from here."""
        return self.app.app_config  # type: ignore[attr-defined,no-any-return]

    @property
    def selected_theme(self) -> str:
        """The theme under the cursor, or an empty string when there is none."""
        index = self._themes.highlighted
        if index is None or not 0 <= index < self._themes.option_count:
            return ""
        return str(self._themes.get_option_at_index(index).prompt)

    # -- lifecycle ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Theme picker first, then the keybindings."""
        yield self._theme_heading
        yield self._themes
        yield self._key_heading
        yield self._keys

    def on_mount(self) -> None:
        """List the keybindings, mark the active theme and take the keyboard.

        A page shown for the first time is mounted and composed after the shell
        pointed it at something, so the handover of the keyboard happens here
        rather than in the shell.
        """
        self._keys.update(self.keys_text())
        self.sync_themes()
        if self.app.active_page == "settings":
            self.focus_themes()

    def keys_text(self) -> str:
        """The bindings the app is running with: defaults, then the user's overrides."""
        bindings = {**DEFAULT_KEYBINDINGS, **dict(self.app_config.keybindings)}
        return "\n".join(
            f"{format_key(key):<{KEY_COLUMN}}{action}" for action, key in bindings.items()
        )

    def sync_themes(self) -> None:
        """Put the cursor on the theme the app is actually using."""
        names = themes.theme_names()
        active = themes.get_theme(self.app_config.theme).name
        self._themes.highlighted = names.index(active) if active in names else None

    def focus_themes(self) -> None:
        """Give the keyboard to the theme picker, once the page is in the DOM."""
        if self.is_attached:
            self._themes.focus()

    # -- the theme picker --------------------------------------------------

    @on(OptionList.OptionSelected)
    def on_theme_selected(self, event: OptionList.OptionSelected) -> None:
        """``enter`` on a theme: switch to it and remember the choice (AC4)."""
        event.stop()
        self.app.set_theme(str(event.option.prompt))  # type: ignore[attr-defined]
