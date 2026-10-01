"""DC-006/AC2: a Settings edit is persisted, applied, and read back.

The card asks for edits that "persist and apply immediately", so every test here
checks both halves: the running app changed, *and* a fresh ``ConfigManager``
standing in for the next run sees the same value.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from textual.pilot import Pilot
from textual.widgets import Input, OptionList

from daylight.app import DaylightApp
from daylight.components.modules.settings_page import DEFAULT_ROWS, SettingsPage
from daylight.managers import ConfigManager, FileManager
from daylight.models import AppConfig

#: Generous ceiling: the window rebuild needs a few turns before it settles.
SETTLE = 60

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def run_async(fn: F) -> F:
    """Run an async test body on a fresh event loop (no pytest-asyncio needed)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A small tree for the listing to open on."""
    (tmp_path / "notes.md").write_text("# Title\n\nbody\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    return tmp_path


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """A config file in a temp dir that does not exist yet."""
    return tmp_path / "config" / "config.json"


def make_app(tree: Path, config_path: Path) -> DaylightApp:
    """An app browsing ``tree`` with its config in ``config_path``.

    The path is handed to the constructor rather than built in memory, so the app
    takes the same "load what was persisted" path a second run would.
    """
    app = DaylightApp(config_path=config_path)
    app.config_manager = ConfigManager(config_path)
    app.file_manager = FileManager()
    app.start_path = tree
    return app


async def settle(pilot: Pilot) -> None:
    """Let pending work and messages finish before asserting."""
    for _ in range(SETTLE):
        await pilot.pause()


async def open_settings(pilot: Pilot, app: DaylightApp) -> SettingsPage:
    """Bring the Settings tab up, once the shell has the listing."""
    for _ in range(SETTLE):
        page = app.files_page
        if page is not None and page.is_ready:
            break
        await pilot.pause()
    await pilot.press("ctrl+4")
    for _ in range(SETTLE):
        if app.active_page == "settings":
            return app.query_one(SettingsPage)
        await pilot.pause()
    raise AssertionError("the settings tab did not come up")


def row_for(page: SettingsPage, name: str) -> int:
    """The index of the default row called ``name``."""
    for index, (row_name, _label, _values) in enumerate(DEFAULT_ROWS):
        if row_name == name:
            return index
    raise AssertionError(name)


def key_row_for(page: SettingsPage, action: str) -> int:
    """The index of the keybinding row that belongs to ``action``.

    The row is display text, so the action is found by its shown label rather
    than by counting positions -- the same way a user finds it.
    """
    listing = page.keys_widget
    label = f"{action.replace('_', ' ')}"
    for index in range(listing.option_count):
        prompt = str(listing.get_option_at_index(index).prompt)
        if prompt.startswith(label):
            return index
    raise AssertionError(action)


# -- the defaults reach the disk and the running app -----------------------


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("show_hidden", "yes", True),
        ("sort_key", "size", "size"),
        ("sort_reverse", "descending", True),
    ],
)
@run_async
async def test_an_edited_default_is_applied_and_saved(
    tree: Path, config_path: Path, name: str, value: str, expected: object
) -> None:
    app = make_app(tree, config_path)
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(pilot)
        await open_settings(pilot, app)
        await settle(pilot)
        app.set_default(name, value)
        await settle(pilot)

        # applied: the running configuration holds it...
        if name == "show_hidden":
            assert app.app_config.show_hidden is expected
            assert app.file_manager.show_hidden is expected
        elif name == "sort_key":
            assert app.app_config.sort_key == expected
        else:
            assert app.app_config.sort_reverse is expected

        # ...and a fresh manager, standing in for the next run, reads it back.
        restored = ConfigManager(config_path).load()
        if name == "show_hidden":
            assert restored.show_hidden is expected
        elif name == "sort_key":
            assert restored.sort_key == expected
        else:
            assert restored.sort_reverse is expected


@run_async
async def test_toggling_a_default_through_the_page_persists(tree: Path, config_path: Path) -> None:
    """The page's own ``enter`` gesture reaches the config file (AC2)."""
    app = make_app(tree, config_path)
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(pilot)
        page = await open_settings(pilot, app)
        await settle(pilot)
        listing = page.defaults_list
        listing.focus()
        listing.highlighted = row_for(page, "show_hidden")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert ConfigManager(config_path).load().show_hidden is True
        assert page.default_value("show_hidden") == "yes"


@run_async
async def test_the_page_shows_the_configured_defaults(tree: Path, config_path: Path) -> None:
    ConfigManager(config_path).save(AppConfig(show_hidden=True, sort_reverse=True))
    app = make_app(tree, config_path)
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(pilot)
        page = await open_settings(pilot, app)
        await settle(pilot)
        assert page.default_value("show_hidden") == "yes"
        assert page.default_value("sort_reverse") == "descending"
        listing = page.defaults_list
        rows = [
            str(listing.get_option_at_index(index).prompt)
            for index in range(listing.option_count)
        ]
        assert any(row.endswith("yes") for row in rows)


# -- a keybinding is persisted and live ------------------------------------


@run_async
async def test_the_on_screen_hints_and_the_palette_show_the_rebound_key(
    tree: Path, config_path: Path
) -> None:
    """AC2 with AC1: a hint or palette line naming the old key would be a lie."""
    ConfigManager(config_path).save(AppConfig(keybindings={"quit": "ctrl+x"}))
    app = make_app(tree, config_path)
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(pilot)
        await open_settings(pilot, app)
        await settle(pilot)
        assert app.hints.hint_text().count("Ctrl+X") == 1
        assert "Ctrl+Q Quit" not in app.hints.hint_text()
        palette = {command.name: command.description for command in app.palette_commands()}
        assert palette["Quit"] == "press Ctrl+X"


@run_async
async def test_pressing_enter_on_a_key_row_rebinds_that_action_and_persists(
    tree: Path, config_path: Path
) -> None:
    """AC2 through the page's own gesture: focus the key list, press enter, retype.

    The first row and a row in the middle are both exercised, so an index that
    happens to be a row number cannot pass for the action it stands for.
    """
    app = make_app(tree, config_path)
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(pilot)
        page = await open_settings(pilot, app)
        await settle(pilot)
        listing = page.keys_widget
        listing.focus()

        for action, key in (("quit", "ctrl+w"), ("command_palette", "ctrl+y")):
            listing.highlighted = key_row_for(page, action)
            await settle(pilot)
            assert page.selected_action == action

            await pilot.press("enter")  # opens the rebind form for this action
            await settle(pilot)
            field = app.screen.query_one(Input)
            field.value = key
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)

            # The running app uses the new key...
            assert app.app_config.keybindings[action] == key
            # ...and a fresh manager, standing in for the next run, reads it back.
            restored = ConfigManager(config_path).load().keybindings
            assert restored[action] == key


@run_async
async def test_an_empty_rebind_form_leaves_the_binding_alone(tree: Path, config_path: Path) -> None:
    """Backing out of the form must not save the key the form was prefilled with."""
    app = make_app(tree, config_path)
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(pilot)
        page = await open_settings(pilot, app)
        await settle(pilot)
        listing = page.keys_widget
        listing.focus()
        listing.highlighted = key_row_for(page, "quit")
        await settle(pilot)

        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("escape")
        await settle(pilot)

        assert app.app_config.keybindings == {}
        assert ConfigManager(config_path).load().keybindings == {}


@run_async
async def test_a_rebound_key_is_saved_and_works_in_the_same_run(
    tree: Path, config_path: Path
) -> None:
    """AC2: the rebound key works from the next keypress, and survives a restart."""
    app = make_app(tree, config_path)
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(pilot)
        page = await open_settings(pilot, app)
        await settle(pilot)
        app.set_keybinding("command_palette", "ctrl+p")
        app.set_keybinding("command_palette", "ctrl+g")
        await settle(pilot)

        assert ConfigManager(config_path).load().keybindings == {"command_palette": "ctrl+g"}
        # The listing shows the effective key, not the shipped default.
        palette_row = next(
            line for line in page.keys_text().splitlines() if line.endswith("command_palette")
        )
        assert palette_row.startswith("Ctrl+G")

        # The override is live: the new key opens the palette...
        await pilot.press("ctrl+g")
        await settle(pilot)
        assert app.screen.__class__.__name__ == "CommandPalette"
        await pilot.press("escape")
        await settle(pilot)
        # ...and the key it replaced does nothing.
        await pilot.press("ctrl+p")
        await settle(pilot)
        assert app.screen.__class__.__name__ != "CommandPalette"
        assert app.app_config.keybindings == {"command_palette": "ctrl+g"}


@run_async
async def test_rebinding_to_the_shipped_key_clears_the_override(
    tree: Path, config_path: Path
) -> None:
    """Typing the default key again is how the user says "undo this"."""
    ConfigManager(config_path).save(AppConfig(keybindings={"quit": "ctrl+x"}))
    app = make_app(tree, config_path)
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(pilot)
        app.set_keybinding("quit", "ctrl+q")
        await settle(pilot)
        assert ConfigManager(config_path).load().keybindings == {}
        assert app.app_config.keybindings == {}


@run_async
async def test_a_saved_override_is_in_force_from_the_first_keypress(
    tree: Path, config_path: Path
) -> None:
    """A key saved by an earlier run applies before the user presses anything."""
    ConfigManager(config_path).save(AppConfig(keybindings={"goto_settings": "ctrl+alt+s"}))
    app = make_app(tree, config_path)
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(pilot)
        await pilot.press("ctrl+alt+s")
        await settle(pilot)
        assert app.active_page == "settings"


@run_async
async def test_the_settings_listing_offers_every_action_it_can_rebind(
    tree: Path, config_path: Path
) -> None:
    app = make_app(tree, config_path)
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(pilot)
        page = await open_settings(pilot, app)
        await settle(pilot)
        listing = page.keys_text()
        for action in ("quit", "command_palette", "view", "open_link", "copy_to_other"):
            assert action in listing


@run_async
async def test_the_page_can_move_between_its_three_listings(
    tree: Path, config_path: Path
) -> None:
    """``d`` and ``k`` are how the page is navigable once it has three lists."""
    app = make_app(tree, config_path)
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(pilot)
        page = await open_settings(pilot, app)
        await settle(pilot)
        assert app.focused is page.themes_list
        await pilot.press("d")
        await settle(pilot)
        assert app.focused is page.defaults_list
        await pilot.press("k")
        await settle(pilot)
        assert app.focused is page.keys_widget


@run_async
async def test_the_option_lists_are_the_ones_the_page_composes(
    tree: Path, config_path: Path
) -> None:
    """Every listing is mounted with the id the styles and the handlers use."""
    app = make_app(tree, config_path)
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(pilot)
        page = await open_settings(pilot, app)
        await settle(pilot)
        for widget_id in ("settings-themes", "settings-defaults", "settings-keys"):
            widget = page.query_one(f"#{widget_id}", OptionList)
            assert widget.option_count > 0
        assert str(page.status.render()) == ""