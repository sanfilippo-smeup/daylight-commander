"""Root Textual application shell: header, page tabs, command palette, theming.

The shell owns everything that is true of the whole app rather than of one page
(FR-500): the persistent header and hint bar, which page is on screen, which
theme is active, and the registry of actions the command palette offers. A page
only knows how to do its own work, which is what keeps the palette from drifting
away from the keybindings -- both are generated from the same ``BINDINGS``.

Navigation follows ADR-005: the page a tab names is mounted into the ``.content``
container. Pages are mounted once and then only shown or hidden, because AC3
asks for a tab round trip that loses nothing -- a listing that was rebuilt on
every switch would also lose the marks, the cursor and the scroll position.
"""

from __future__ import annotations

import os
from functools import partial
from pathlib import Path
from typing import ClassVar

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container
from textual.css.query import NoMatches
from textual.reactive import var
from textual.widget import Widget
from textual.widgets import Tabs

from daylight import themes
from daylight.components import file_table
from daylight.components.command_palette import DaylightCommandProvider, PaletteCommand
from daylight.components.modules.file_list import FilesPage, LocationChanged
from daylight.components.modules.properties_page import PropertiesPage
from daylight.components.modules.settings_page import SettingsPage
from daylight.components.modules.stats_page import StatsPage
from daylight.components.modules.viewers_page import ViewersPage
from daylight.components.shell import KeyHintBar, ShellHeader, format_key
from daylight.config import DEFAULT_KEYBINDINGS
from daylight.managers import ConfigManager, FileManager
from daylight.models import AppConfig

__all__ = ["DEFAULT_PAGE", "PAGE_LABELS", "DaylightApp"]

#: The page every tab opens, in tab order. A tab's id is its page name.
PAGE_LABELS: dict[str, str] = {
    "files": "Files",
    "viewers": "Viewers",
    "properties": "Properties",
    "settings": "Settings",
    "stats": "Stats",
}

#: The page the app starts on.
DEFAULT_PAGE = "files"

#: Footer display order (DC-019): the frequent file operations first, then the
#: global Palette / Switch pane / Quit keys. Actions the order does not name --
#: every other page's own keys -- keep their page-then-app declaration order,
#: ahead of the named ones.
HINT_ORDER: dict[str, int] = {
    "copy_to_other": 0,
    "move_to_other": 1,
    "new_directory": 2,
    "delete": 3,
    "command_palette": 4,
    "switch_panel": 5,
    "quit": 6,
}


class DaylightApp(App[None]):
    """Daylight Commander application."""

    TITLE = "Daylight Commander"
    CSS_PATH = [
        "styles/index.tcss",
        "styles/shell.tcss",
        "styles/files.tcss",
        "styles/operations.tcss",
        "styles/viewer.tcss",
        "styles/stats.tcss",
    ]
    BINDINGS = [
        # The only two app-wide hints the footer has room for; the tab keys are
        # deliberately hidden from it and live in the palette and the tab strip.
        # Each binding carries its action name as its ``id`` so a keybinding
        # override from the Settings page can be applied at runtime (AC2).
        Binding(
            DEFAULT_KEYBINDINGS["command_palette"],
            "command_palette",
            "Palette",
            id="command_palette",
        ),
        Binding(DEFAULT_KEYBINDINGS["quit"], "quit", "Quit", id="quit"),
        Binding(
            DEFAULT_KEYBINDINGS["goto_files"], "goto_files", "Files", show=False, id="goto_files"
        ),
        Binding(
            DEFAULT_KEYBINDINGS["goto_viewers"],
            "goto_viewers",
            "Viewers",
            show=False,
            id="goto_viewers",
        ),
        Binding(
            DEFAULT_KEYBINDINGS["goto_properties"],
            "goto_properties",
            "Properties",
            show=False,
            id="goto_properties",
        ),
        Binding(
            DEFAULT_KEYBINDINGS["goto_settings"],
            "goto_settings",
            "Settings",
            show=False,
            id="goto_settings",
        ),
        Binding(
            DEFAULT_KEYBINDINGS["goto_stats"],
            "goto_stats",
            "Stats",
            show=False,
            id="goto_stats",
        ),
    ]
    #: The palette keeps Textual's own system commands and adds every Daylight one.
    COMMANDS: ClassVar[set[object]] = {*App.COMMANDS, DaylightCommandProvider}
    COMMAND_PALETTE_BINDING: ClassVar[str] = DEFAULT_KEYBINDINGS["command_palette"]

    #: The page class behind each tab (ADR-005).
    PAGES: ClassVar[dict[str, type[Widget]]] = {
        "files": FilesPage,
        "viewers": ViewersPage,
        "properties": PropertiesPage,
        "settings": SettingsPage,
        "stats": StatsPage,
    }

    #: App-level actions offered by the palette: ``(title, action, description)``.
    SHELL_ACTIONS: ClassVar[tuple[tuple[str, str, str], ...]] = (
        ("Go to Files", "goto_files", "Browse files in the dual pane"),
        ("Go to Viewers", "goto_viewers", "Read the file across the whole pane"),
        ("Go to Properties", "goto_properties", "Describe the entry across the whole pane"),
        ("Go to Settings", "goto_settings", "Theme and keybindings"),
        ("Go to Stats", "goto_stats", "Directory statistics for the active pane"),
        ("Next theme", "next_theme", "Switch to the next theme in the catalogue"),
    )

    active_page: var[str] = var(DEFAULT_PAGE, init=False)

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
        #: Pages that have been mounted, by page name. A page is mounted once and
        #: then only shown or hidden, so its state outlives the tab.
        self._pages: dict[str, Widget] = {}
        self.header = ShellHeader(self.TITLE, PAGE_LABELS, active=DEFAULT_PAGE)
        self.hints = KeyHintBar()
        super().__init__()

    # -- queries -----------------------------------------------------------

    @property
    def tabs(self) -> Tabs:
        """The page tab strip in the header."""
        return self.header.tabs

    @property
    def files_page(self) -> FilesPage | None:
        """The Files page, once it has been mounted."""
        try:
            return self.query_one(FilesPage)
        except NoMatches:
            return None

    @property
    def settings_page(self) -> SettingsPage | None:
        """The Settings page, once it has been mounted."""
        try:
            return self.query_one(SettingsPage)
        except NoMatches:
            return None

    def current_path(self) -> Path:
        """The path the header shows: the directory of the active pane.

        Before the Files page has composed its panels there is no active pane to
        ask, and the configured start path is the honest answer.
        """
        files = self.files_page
        if files is None or not files.is_ready:
            return self.start_path
        return files.active.directory

    def view_target(self) -> Path | None:
        """What the Viewers and Properties tabs show.

        The file last opened in a panel wins, so switching to the Viewers tab
        after ``f3`` shows that file; otherwise it follows the listing cursor.
        """
        files = self.files_page
        if files is None or not files.is_ready:
            return None
        if files.view_target is not None:
            return files.view_target
        entry = files.target_entry()
        return entry.path if entry is not None else None

    def key_hints(self) -> list[tuple[str, str]]:
        """The keybinding hints of the current view, in footer order.

        A page binding and an app binding can share a key; the page wins, because
        it is the more specific one and the one the user is actually in. A key the
        user rebound in Settings is shown as the new key, not the shipped one
        (AC2): a hint that names a key which no longer works is worse than none.

        The bar reads in :data:`HINT_ORDER`'s sequence (DC-019): the file
        operations, then Palette, Switch pane, Quit. Actions the order does not
        name keep their page-then-app declaration order, ahead of the named
        ones.
        """
        page = self._pages.get(self.active_page)
        bindings: list[BindingType] = []
        if page is not None:
            bindings.extend(type(page).BINDINGS)
        bindings.extend(self.BINDINGS)
        overrides = self.app_config.keybindings
        collected: list[tuple[int, int, str, str]] = []
        seen: set[str] = set()
        for position, binding in enumerate(Binding.make_bindings(bindings)):
            if not binding.show:
                continue
            # A rebound key is the key that works, so it is also the key the
            # hint bar has to show (AC2 with AC1's on-screen-hints promise).
            key = overrides.get(binding.id or "", binding.key)
            if key in seen:
                continue
            seen.add(key)
            rank = HINT_ORDER.get(binding.id or "", -1)
            collected.append((rank, position, key, binding.description or binding.action))
        collected.sort()
        return [(key, description) for _, _, key, description in collected]

    # -- lifecycle ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        """The persistent shell: header, the content host, the hint bar.

        The first page is part of the composition, so the app opens on a listing
        rather than on an empty frame; every other page is mounted into the same
        container the first time its tab asks for it.
        """
        yield self.header
        with Container(id="content", classes="content"):
            self._pages[DEFAULT_PAGE] = self._make_page(DEFAULT_PAGE)
            yield self._pages[DEFAULT_PAGE]
        yield self.hints

    def on_mount(self) -> None:
        """Register every theme and apply the configured one.

        The first page is already on screen and preparing itself: the tab strip
        reports its active tab while the app is still starting, and that is what
        hands the keyboard to the listing. Doing it here instead would be a
        deferred focus that lands on top of the user's first keypress.
        """
        for theme in themes.THEMES.values():
            self.register_theme(themes.to_textual_theme(theme))
        self.apply_theme(self.app_config.theme)
        # A keybinding saved by an earlier run has to work from the first
        # keypress, not from the next restart.
        self.apply_keymap()
        self.apply_defaults()

    def get_css_variables(self) -> dict[str, str]:
        """Publish the active theme's tokens to every stylesheet (ADR-004).

        Textual's color system already provides most of these; the app's own
        tokens are merged on top so a theme switch changes them in one place and
        no ``.tcss`` file ever carries a color literal.
        """
        variables = super().get_css_variables()
        variables.update(themes.get_css_variables(themes.get_theme(self.app_config.theme)))
        return variables

    # -- pages and tabs ----------------------------------------------------

    def page(self, name: str) -> Widget:
        """The page widget for ``name``, mounted into ``#content`` the first time."""
        page = self._pages.get(name)
        if page is None:
            page = self._make_page(name)
            self._pages[name] = page
            self.query_one("#content").mount(page)
        return page

    def _make_page(self, name: str) -> Widget:
        """Build a page by name; the Files page also needs the start path."""
        if name == "files":
            return FilesPage(
                self.start_path,
                file_manager=self.file_manager,
                config_manager=self.config_manager,
            )
        return self.PAGES[name]()

    def show_page(self, name: str) -> None:
        """Make ``name`` the visible page and give it the keyboard (AC3).

        Every other page stays mounted and is merely hidden, so coming back to a
        tab finds the listing, the marks and the scroll position as they were.
        """
        if name not in self.PAGES:
            return
        try:
            self.query_one("#content")
        except NoMatches:
            return  # the shell is not composed yet; ``on_mount`` opens the first page
        page = self.page(name)
        for other, widget in self._pages.items():
            widget.display = other == name
        self.active_page = name
        if self.tabs.active != name:
            self.tabs.active = name
        self._prepare_page(page)
        self.update_path()
        self.refresh_hints()

    def _prepare_page(self, page: Widget) -> None:
        """Point a freshly shown page at what it should display.

        A page that is still being composed prepares itself in its own
        ``on_mount``; asking it here would query panels that do not exist yet.
        """
        if isinstance(page, FilesPage):
            if page.is_ready:
                page.sync_active()
        elif isinstance(page, (ViewersPage, PropertiesPage)):
            page.open_path(self.view_target())
            # The page is focusable so its own keys work; focusing it here is
            # what puts them within reach (AC3).
            page.focus()
        elif isinstance(page, SettingsPage):
            page.sync_themes()
            page.focus_themes()
        elif isinstance(page, StatsPage):
            # A fresh show recomputes only what the per-directory cache has not
            # already got (DC-023, AC6); the page is focusable so ctrl+r works.
            page.refresh_stats()
            page.focus()

    @on(Tabs.TabActivated)
    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        """Show the page the newly activated tab names (ADR-005)."""
        event.stop()
        tab = event.tab
        if tab is not None and tab.id:
            self.show_page(tab.id)

    @on(LocationChanged)
    def on_location_changed(self, event: LocationChanged) -> None:
        """Follow the listing's path into the persistent header (AC1)."""
        del event
        self.update_path()
        # The Stats tab follows the active pane's directory while it is the one
        # on screen (DC-023, AC6); hidden, it refreshes on its next show.
        if self.active_page == "stats":
            stats = self._pages.get("stats")
            if isinstance(stats, StatsPage):
                stats.refresh_stats()

    def update_path(self) -> None:
        """Show the directory being browsed in the header."""
        self.header.set_path(file_table.format_breadcrumb(self.current_path()))

    def refresh_hints(self) -> None:
        """Re-render the hint bar for the page in view (AC1)."""
        self.hints.show_hints(self.key_hints())

    # -- actions -----------------------------------------------------------

    def action_goto_files(self) -> None:
        """``ctrl+1``: show the Files page."""
        self.show_page("files")

    def action_goto_viewers(self) -> None:
        """``ctrl+2``: show the Viewers page."""
        self.show_page("viewers")

    def action_goto_properties(self) -> None:
        """``ctrl+3``: show the Properties page."""
        self.show_page("properties")

    def action_goto_settings(self) -> None:
        """``ctrl+4``: show the Settings page."""
        self.show_page("settings")

    def action_goto_stats(self) -> None:
        """``ctrl+5``: show the Stats page (DC-023)."""
        self.show_page("stats")

    def action_next_theme(self) -> None:
        """Switch to the next theme in the catalogue."""
        names = themes.theme_names()
        current = themes.get_theme(self.app_config.theme).name
        self.set_theme(names[(names.index(current) + 1) % len(names)])

    def run_page_action(self, name: str, action: str) -> None:
        """Show ``name`` and run one of its actions: the palette's remote control.

        The page is shown first so the action happens where the user can see it,
        and so the keyboard is on the page the action belongs to.
        """
        self.show_page(name)
        page = self.page(name)
        method = getattr(page, f"action_{action}", None)
        if method is None:
            self.notify(
                f"no action {action!r} on the {PAGE_LABELS[name]} page", severity="warning"
            )
            return
        method()

    # -- themes ------------------------------------------------------------

    def apply_theme(self, name: str) -> None:
        """Activate the named theme and re-resolve the stylesheet (ADR-004)."""
        theme = themes.get_theme(name)
        self.register_theme(themes.to_textual_theme(theme))
        self.theme = theme.name
        self.refresh_css()

    def set_theme(self, name: str) -> None:
        """Switch to ``name`` and remember the choice in the user config (AC4)."""
        if name not in themes.THEMES:
            self.notify(f"unknown theme: {name}", severity="warning")
            return
        self.app_config = self.config_manager.update(theme=name)
        self.apply_theme(name)
        settings = self.settings_page
        if settings is not None:
            settings.sync_themes()
        self.notify(f"theme: {name}")

    # -- settings editing (AC2) --------------------------------------------

    def set_keybinding(self, action: str, key: str) -> AppConfig:
        """Persist one keybinding override and make it the binding that is used.

        The override is written to the config file and pushed into the running
        app's keymap, so the key the Settings page shows is the key that works
        from the next keypress on -- and the one that is read back on restart
        (AC2). An empty ``key`` clears the override and restores the default.
        """
        clean = key.strip()
        if clean == DEFAULT_KEYBINDINGS.get(action):
            return self.clear_keybinding(action)
        overrides = dict(self.app_config.keybindings)
        if clean:
            overrides[action] = clean
        else:
            overrides.pop(action, None)
        self.app_config = self.config_manager.update(keybindings=overrides)
        self.apply_keymap()
        settings = self.settings_page
        if settings is not None:
            settings.sync_keys()
        self.refresh_hints()
        self.notify(f"{action}: {clean or DEFAULT_KEYBINDINGS.get(action, '?')}")
        return self.app_config

    def clear_keybinding(self, action: str) -> AppConfig:
        """Drop the user's override for ``action`` and go back to the default."""
        overrides = dict(self.app_config.keybindings)
        overrides.pop(action, None)
        self.app_config = self.config_manager.update(keybindings=overrides)
        self.apply_keymap()
        settings = self.settings_page
        if settings is not None:
            settings.sync_keys()
        self.refresh_hints()
        return self.app_config

    def set_default(self, name: str, value: str) -> AppConfig:
        """Persist one of the editable defaults and apply it to the listing.

        The value arrives as the word the Settings row shows (``yes``/``no``, a
        sort key, ``ascending``/``descending``), is converted here to what the
        config stores, and is then applied to the running app -- the hidden-files
        toggle reaches the file manager and the sort reaches the panels -- so an
        edit is visible immediately and after a restart (AC2).
        """
        if name == "show_hidden":
            self.app_config = self.config_manager.update(show_hidden=value == "yes")
            self.file_manager.show_hidden = self.app_config.show_hidden
        elif name == "sort_key":
            self.app_config = self.config_manager.update(sort_key=value)
        elif name == "sort_reverse":
            self.app_config = self.config_manager.update(sort_reverse=value == "descending")
        else:
            self.notify(f"unknown default: {name}", severity="warning")
            return self.app_config
        self.apply_defaults()
        return self.app_config

    def apply_defaults(self) -> None:
        """Push the configured defaults into the running listing (AC2)."""
        self.file_manager.show_hidden = self.app_config.show_hidden
        files = self.files_page
        if files is None or not files.is_ready:
            return
        for panel in files.panels:
            panel.sort_key = self.app_config.sort_key
            panel.sort_reverse = self.app_config.sort_reverse
            panel.reload()

    def apply_keymap(self) -> None:
        """Rebuild the runtime keymap from the configured overrides (AC2).

        Textual overrides a binding by its ``id``, so every editable action
        carries its action name as the binding id and the keymap is simply the
        override mapping. That is what makes a rebound key work immediately
        rather than only after a restart.
        """
        self.set_keymap(dict(self.app_config.keybindings))

    # -- the command palette (FR-503, AC2) ---------------------------------

    def palette_commands(self) -> list[PaletteCommand]:
        """Every action the app can run, as palette entries (AC2).

        Built from the ``BINDINGS`` the keys come from -- app-wide, per page and
        all of them, including the ones the hint bar has no room to show -- plus
        the tab switches and one entry per theme. Nothing is written down twice,
        so an action cannot be reachable by key and missing here, and a search
        result is never the same action twice.

        The tab switches come from ``SHELL_ACTIONS`` only: they already reach
        ``action_goto_*``, and listing them a second time here as well is what
        made every page show up twice under a "go to" search.
        """
        commands: list[PaletteCommand] = []
        for title, action, description in self.SHELL_ACTIONS:
            commands.append(
                PaletteCommand(title, description, partial(self.call_next, self.run_action, action))
            )
        overrides = self.app_config.keybindings
        for binding in Binding.make_bindings(list(self.BINDINGS)):
            key = overrides.get(binding.id or "", binding.key)
            commands.append(
                PaletteCommand(
                    binding.description or binding.action,
                    f"press {format_key(key)}",
                    partial(self.call_next, self.run_action, binding.action),
                )
            )
        for name in themes.theme_names():
            commands.append(
                PaletteCommand(
                    f"Theme: {name}",
                    "Switch the theme and remember it",
                    partial(self.set_theme, name),
                )
            )
        for name, label in PAGE_LABELS.items():
            page_class = self.PAGES[name]
            for binding in Binding.make_bindings(list(page_class.BINDINGS)):
                title = binding.action.replace("_", " ").capitalize()
                key = overrides.get(binding.id or "", binding.key)
                commands.append(
                    PaletteCommand(
                        f"{label}: {title}",
                        f"press {format_key(key)}",
                        partial(self.run_page_action, name, binding.action),
                    )
                )
        return commands
