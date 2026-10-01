"""The Settings tab: the theme, the defaults, and which keys are bound (FR-601).

Three editable things, and all three persist through
:class:`~daylight.managers.ConfigManager` the moment they change:

* the **theme**, picked from the catalogue (DC-005) -- the same
  :meth:`DaylightApp.set_theme` the command palette calls;
* the **defaults** -- hidden files and the sort order the listing starts in,
  listed as rows whose value is cycled with ``enter``;
* the **keybindings** -- one row per action, ``enter`` asks for a new key and
  stores it as an override.

Nothing about editing is applied only in the widget: a value the user changed is
written to the config file, handed to the running app (:meth:`DaylightApp.
set_keybinding`, :meth:`DaylightApp.set_default`) and so survives a restart. That
is what the page's metric means -- an edit that only changed the listing on
screen would not be an edit at all.
"""

from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import OptionList, Static

from daylight import themes
from daylight.components.shell import format_key
from daylight.config import DEFAULT_KEYBINDINGS
from daylight.forms import KEY_FORM
from daylight.managers.file_manager import SORT_KEYS
from daylight.models import AppConfig
from daylight.modals import FormScreen

__all__ = ["DEFAULT_ROWS", "SettingsPage"]

#: The editable defaults, as ``(id, label, values)``: the row cycles its values.
DEFAULT_ROWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("show_hidden", "Hidden files", ("no", "yes")),
    ("sort_key", "Sort by", SORT_KEYS),
    ("sort_reverse", "Sort order", ("ascending", "descending")),
)

#: Width of the key column in the keybinding listing.
KEY_COLUMN = 12


def _yes_no(value: bool) -> str:
    """Render a boolean default as the word the row shows."""
    return "yes" if value else "no"


class SettingsPage(Vertical):
    """Theme picker, the editable defaults and the effective keybindings."""

    DEFAULT_CLASSES: ClassVar[str] = "settings-page"

    BINDINGS = [
        Binding("d", "focus_defaults", "Defaults", show=True),
        Binding("k", "focus_keys", "Keys", show=True),
    ]

    def __init__(self) -> None:
        self._theme_heading = Static("Theme", classes="settings-heading")
        self._themes = OptionList(*themes.theme_names(), id="settings-themes")
        self._default_heading = Static("Defaults", classes="settings-heading")
        self._defaults = OptionList(id="settings-defaults")
        self._key_heading = Static("Keybindings", classes="settings-heading")
        self._keys = OptionList(id="settings-keys")
        self._status = Static("", id="settings-status", classes="settings-status")
        super().__init__(id="settings-page")

    # -- queries -----------------------------------------------------------

    @property
    def themes_list(self) -> OptionList:
        """The theme picker."""
        return self._themes

    @property
    def defaults_list(self) -> OptionList:
        """The list of editable defaults."""
        return self._defaults

    @property
    def keys_widget(self) -> OptionList:
        """The keybinding listing, one selectable row per action."""
        return self._keys

    @property
    def status(self) -> Static:
        """The line that reports what was just saved."""
        return self._status

    @property
    def app_config(self) -> AppConfig:
        """The running configuration: the overrides come from here."""
        return self.app.app_config  # type: ignore[attr-defined,no-any-return]

    @property
    def selected_theme(self) -> str:
        """The theme under the cursor, or an empty string when there is none."""
        return self._selected_prompt(self._themes)

    @property
    def selected_action(self) -> str:
        """The keybinding action under the cursor, or an empty string.

        The keybinding rows are display text, so the action is recovered from the
        row's position: ``sync_keys`` renders one row per action in the order
        :meth:`bindings` gives them, so the row at the cursor is that list's
        entry at the same index.
        """
        index = self._option_index(self._keys)
        if index is None:
            return ""
        actions = list(self.bindings())
        if index >= len(actions):
            return ""
        return actions[index]

    @staticmethod
    def _option_index(widget: OptionList) -> int | None:
        """The option index at the cursor of ``widget``, or ``None`` when off."""
        index = widget.highlighted
        if index is None or not 0 <= index < widget.option_count:
            return None
        return index

    @staticmethod
    def _selected_prompt(widget: OptionList) -> str:
        """Return the prompt at the cursor of ``widget``, or an empty string."""
        index = SettingsPage._option_index(widget)
        if index is None:
            return ""
        return str(widget.get_option_at_index(index).prompt)

    # -- lifecycle ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Theme picker, the defaults, then the keybindings."""
        yield self._theme_heading
        yield self._themes
        yield self._default_heading
        yield self._defaults
        yield self._key_heading
        yield self._keys
        yield self._status

    def on_mount(self) -> None:
        """Fill both listings, mark the active theme and take the keyboard.

        A page shown for the first time is mounted and composed after the shell
        pointed it at something, so the handover of the keyboard happens here
        rather than in the shell.
        """
        self.sync_defaults()
        self.sync_keys()
        self.sync_themes()
        if self.app.active_page == "settings":
            self.focus_themes()

    # -- the listings ------------------------------------------------------

    def bindings(self) -> dict[str, str]:
        """The bindings the app runs with: defaults, then the user's overrides."""
        return {**DEFAULT_KEYBINDINGS, **dict(self.app_config.keybindings)}

    def keys_text(self) -> str:
        """The keybinding listing as one line per action, for tests and hints."""
        return "\n".join(
            f"{format_key(key):<{KEY_COLUMN}}{action}" for action, key in self.bindings().items()
        )

    def default_value(self, name: str) -> str:
        """The current value of the default called ``name``, as its row shows it."""
        config = self.app_config
        if name == "show_hidden":
            return _yes_no(config.show_hidden)
        if name == "sort_reverse":
            return "descending" if config.sort_reverse else "ascending"
        return config.sort_key

    def sync_defaults(self) -> None:
        """Rebuild the defaults listing, with the cursor on the running values."""
        rows = [
            f"{label:<{KEY_COLUMN}}{self.default_value(name)}" for name, label, _ in DEFAULT_ROWS
        ]
        self._defaults.clear_options()
        self._defaults.add_options(rows)
        self._defaults.highlighted = 0

    def sync_keys(self) -> None:
        """Rebuild the keybinding listing from the effective bindings."""
        self._keys.clear_options()
        self._keys.add_options(
            [
                f"{action.replace('_', ' '):<{KEY_COLUMN + 4}}{format_key(key)}"
                for action, key in self.bindings().items()
            ]
        )
        self._keys.highlighted = 0

    def sync_themes(self) -> None:
        """Put the cursor on the theme the app is actually using."""
        names = themes.theme_names()
        active = themes.get_theme(self.app_config.theme).name
        self._themes.highlighted = names.index(active) if active in names else None

    def focus_themes(self) -> None:
        """Give the keyboard to the theme picker, once the page is in the DOM."""
        if self.is_attached:
            self._themes.focus()

    def focus_list(self, widget: OptionList) -> None:
        """Give the keyboard to one of the other listings."""
        if self.is_attached:
            widget.focus()

    # -- editing -----------------------------------------------------------

    @on(OptionList.OptionSelected, "#settings-themes")
    def on_theme_selected(self, event: OptionList.OptionSelected) -> None:
        """``enter`` on a theme: switch to it and remember the choice (AC4)."""
        event.stop()
        self.app.set_theme(str(event.option.prompt))  # type: ignore[attr-defined]

    @on(OptionList.OptionSelected, "#settings-defaults")
    def on_default_selected(self, event: OptionList.OptionSelected) -> None:
        """``enter`` on a default: cycle it, persist it and apply it (AC2)."""
        event.stop()
        index = event.option_index
        if not 0 <= index < len(DEFAULT_ROWS):
            return
        name, label, values = DEFAULT_ROWS[index]
        self.app.set_default(name, next_value(self.default_value(name), values))  # type: ignore[attr-defined]
        self._status.update(f" {label}: {self.default_value(name)}")
        self.sync_defaults()

    @on(OptionList.OptionSelected, "#settings-keys")
    def on_key_selected(self, event: OptionList.OptionSelected) -> None:
        """``enter`` on a keybinding: ask for a new key and store it (AC2)."""
        event.stop()
        action = self.selected_action
        if not action:
            return
        current = self.bindings()[action]
        self.app.push_screen(  # type: ignore[attr-defined]
            FormScreen(KEY_FORM.with_defaults(key=current), title=f"Rebind {action}"),
            lambda values: self._rebind_done(action, values),
        )

    def _rebind_done(self, action: str, values: dict[str, str] | None) -> None:
        """Store the key the rebinding form asked for."""
        if not values:
            return
        key = values.get("key", "").strip()
        if not key:
            return
        self.app.set_keybinding(action, key)  # type: ignore[attr-defined]
        self._status.update(f" {action}: {format_key(key)}")
        self.sync_keys()

    # -- moving between the listings ---------------------------------------

    def action_focus_defaults(self) -> None:
        """``d``: go to the defaults listing."""
        self.focus_list(self._defaults)

    def action_focus_keys(self) -> None:
        """``k``: go to the keybindings listing."""
        self.focus_list(self._keys)


def next_value(current: str, values: tuple[str, ...]) -> str:
    """Return the value after ``current`` in ``values``, wrapping around.

    A stored value that is no longer offered (a config written by a later
    version) starts the cycle from the top rather than raising.
    """
    if current not in values or not values:
        return values[0] if values else current
    return values[(values.index(current) + 1) % len(values)]