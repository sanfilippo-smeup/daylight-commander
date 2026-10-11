"""Files page tests: dual pane, focus model, navigation, filter, sort, hidden.

These drive the real app through Textual's pilot, so they cover the acceptance
criteria end to end (the manager-side listing is covered in
``test_file_listing.py``). ``pytest-asyncio`` is not a project dependency, so
``run_async`` bridges the async scenarios onto plain sync test functions.
"""

from __future__ import annotations

import asyncio
import functools
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

import pytest
from textual.coordinate import Coordinate
from textual.events import MouseScrollDown, MouseScrollUp
from textual.pilot import Pilot
from textual.widgets import Input, Label

from daylight.app import DaylightApp
from daylight.components.modules.file_list import (
    PANEL_NAMES,
    SCROLL_STEP,
    FilePanel,
    FilesPage,
)
from daylight.managers import ConfigManager, FileManager
from daylight.models import AppConfig, DirEntry

#: Generous ceiling: worker threads and the window rebuild need a few turns.
SETTLE = 60

#: Terminal size used for every pilot scenario.
SIZE = (120, 40)

#: A short terminal, so a listing is bigger than the viewport and the virtual
#: window has to slide for the scrolling tests to mean anything.
SMALL = (120, 14)

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def run_async(fn: F) -> F:
    """Run an async test body on a fresh event loop (no pytest-asyncio needed)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Deterministic listing fixture used by the page tests."""
    (tmp_path / "alpha.txt").write_text("a" * 10, encoding="utf-8")
    (tmp_path / "beta.py").write_text("b" * 3000, encoding="utf-8")
    (tmp_path / "gamma.md").write_text("g" * 20, encoding="utf-8")
    (tmp_path / ".hidden").write_text("secret", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "deep.md").write_text("dd", encoding="utf-8")
    return tmp_path


def make_app(tree: Path, config_path: Path, show_hidden: bool = False) -> DaylightApp:
    """Build an app pinned to ``tree`` with an isolated config file."""
    app = DaylightApp(AppConfig(show_hidden=show_hidden))
    app.start_path = tree
    app.config_manager = ConfigManager(config_path)
    app.file_manager = FileManager(show_hidden=show_hidden)
    return app


def fake_entry(index: int) -> DirEntry:
    """A synthetic in-memory row, so no disk access is needed for big listings."""
    return DirEntry(
        name=f"f{index:06d}.dat",
        path=Path("/nowhere") / f"f{index:06d}.dat",
        is_dir=False,
        is_hidden=False,
        is_symlink=False,
        size=index,
        modified=0.0,
        permissions="rw-r--r--",
        kind="file",
        suffix="dat",
    )


async def settle(pilot: Pilot, panel: FilePanel) -> None:
    """Wait until ``panel`` finished a listing and has rows."""
    for _ in range(SETTLE):
        if not panel.loading and panel.row_count:
            return
        await pilot.pause()
    raise AssertionError("panel did not finish listing")


async def wait_for(pilot: Pilot, predicate: Callable[[], bool]) -> None:
    """Pause until ``predicate`` holds, or fail after a bounded number of turns."""
    for _ in range(SETTLE):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError("condition never became true")


def plain_names(panel: FilePanel) -> list[str]:
    """Row names without the leading icon."""
    return [name.split(" ", 1)[1] for name in panel.visible_names()]


# -- AC2: two panels, exactly one active -----------------------------------


@run_async
async def test_two_panels_each_list_a_directory(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left, right = page.panels
        await settle(pilot, left)
        await settle(pilot, right)
        assert left.directory == right.directory == tree
        assert left.row_count == right.row_count
        assert left.row_count > 0


@run_async
async def test_exactly_one_panel_is_active_and_focused(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        assert page.active_panel == "left"
        assert page.active_names() == ["left"]
        assert left.has_class("active") is True
        assert page.panel("right").has_class("active") is False
        assert app.focused is left.table


@run_async
async def test_active_split_moves_to_the_other_panel(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await pilot.press("ctrl+t")
        assert page.active_panel == "right"
        assert page.active_names() == ["right"]
        assert app.focused is page.panel("right").table
        await pilot.press("ctrl+t")
        assert page.active_panel == "left"
        assert page.active_names() == ["left"]


@run_async
async def test_set_active_panel_rejects_unknown_names(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page.panel("left"))
        with pytest.raises(ValueError):
            page.set_active_panel("middle")
        assert page.active_panel == "left"


@run_async
async def test_setting_the_same_active_panel_keeps_one_active(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page.panel("left"))
        page.set_active_panel("left")
        assert page.active_names() == ["left"]


# -- AC4: the inactive panel exposes a mount point -------------------------


@run_async
async def test_both_panels_expose_a_stable_mount_point(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        assert left.mount_point.id == "mount-left"
        assert page.panel("right").mount_point.id == "mount-right"
        assert left.mount_point is not page.panel("right").mount_point


@run_async
async def test_inactive_panel_is_the_other_side(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        assert page.inactive is page.panel("right")
        await pilot.press("ctrl+t")
        assert page.inactive is page.panel("left")


@run_async
async def test_mount_point_can_receive_a_viewer(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page.panel("left"))
        await page.inactive.mount_point.mount(Label("viewer"))
        await pilot.pause()
        assert str(page.inactive.mount_point.query_one(Label).render()) == "viewer"


@run_async
async def test_tables_use_row_cursor_and_zebra_stripes(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        assert left.table.cursor_type == "row"
        assert left.table.zebra_stripes is True


# -- AC3: navigation and breadcrumb ----------------------------------------


@run_async
async def test_breadcrumb_shows_the_current_path(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        assert str(tree) in left.breadcrumb_text()
        assert "items" in left.breadcrumb_text()


@run_async
async def test_enter_navigates_into_a_directory(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        index = plain_names(left).index("docs")
        left.table.cursor_coordinate = Coordinate(index, 0)
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(pilot, lambda: left.directory == tree / "docs")
        assert "deep.md" in plain_names(left)


@run_async
async def test_enter_on_a_file_does_not_navigate(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        index = plain_names(left).index("alpha.txt")
        left.table.cursor_coordinate = Coordinate(index, 0)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert left.directory == tree


@run_async
async def test_backspace_navigates_up(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        left.go_to(tree / "docs")
        await settle(pilot, left)
        assert left.directory == tree / "docs"
        await pilot.press("backspace")
        await wait_for(pilot, lambda: left.directory == tree)
        assert left.directory == tree


@run_async
async def test_dot_dot_row_navigates_up(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        left.go_to(tree / "docs")
        await settle(pilot, left)
        assert plain_names(left)[0] == ".."
        left.table.cursor_coordinate = Coordinate(0, 0)
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(pilot, lambda: left.directory == tree)
        assert left.directory == tree


@run_async
async def test_dot_dot_row_is_absent_at_the_root(tmp_path: Path) -> None:
    app = make_app(tmp_path, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        left.go_to(Path("/"))
        await settle(pilot, left)
        assert ".." not in plain_names(left)


@run_async
async def test_ascend_at_the_root_is_a_no_op(tmp_path: Path) -> None:
    app = make_app(tmp_path, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        left.go_to(Path("/"))
        await settle(pilot, left)
        left.ascend()
        await pilot.pause()
        assert left.directory == Path("/")


@run_async
async def test_navigation_only_affects_the_active_panel(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left, right = page.panels
        await settle(pilot, left)
        left.go_to(tree / "docs")
        await settle(pilot, left)
        await pilot.press("backspace")
        await wait_for(pilot, lambda: left.directory == tree)
        assert right.directory == tree


# -- AC5: filter and sort --------------------------------------------------


@run_async
async def test_filter_as_you_type_narrows_the_active_panel(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await pilot.press("slash")
        assert isinstance(app.focused, Input)
        await pilot.press(*"alpha")
        await wait_for(pilot, lambda: left.row_count == 1)
        assert plain_names(left) == ["alpha.txt"]


@run_async
async def test_filter_only_narrows_the_active_panel(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left, right = page.panels
        await settle(pilot, left)
        before = right.row_count
        await pilot.press("slash")
        await pilot.press(*"alpha")
        await wait_for(pilot, lambda: left.row_count == 1)
        assert right.row_count == before


@run_async
async def test_filter_clearing_restores_every_row(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        total = left.row_count
        await pilot.press("slash")
        await pilot.press(*"alpha")
        await wait_for(pilot, lambda: left.row_count == 1)
        await pilot.press(*("backspace",) * 5)
        await wait_for(pilot, lambda: left.row_count == total)
        assert left.row_count == total


# -- DC-008: the filter clears on directory entry --------------------------


@run_async
async def test_entering_a_subdirectory_clears_the_filter(tree: Path, tmp_path: Path) -> None:
    """AC1: entering a folder drops the filter so the new listing is whole."""
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await pilot.press("slash")
        await pilot.press(*"do")
        await wait_for(pilot, lambda: left.row_count == 1)
        assert plain_names(left) == ["docs"]
        await pilot.press("enter")  # confirm the filter, focus back on the table
        await pilot.press("enter")  # enter the docs folder
        await wait_for(pilot, lambda: left.directory == tree / "docs")
        await settle(pilot, left)
        assert left.filter_text == ""
        assert page.filter_input.value == ""
        assert plain_names(left) == ["..", "deep.md"]


@run_async
async def test_navigating_up_clears_the_filter(tree: Path, tmp_path: Path) -> None:
    """AC2: ``backspace`` to the parent drops the filter too."""
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        left.go_to(tree / "docs")
        await settle(pilot, left)
        left.set_filter("deep")
        await wait_for(pilot, lambda: left.row_count == 1)
        assert plain_names(left) == ["deep.md"]
        await pilot.press("backspace")
        await wait_for(pilot, lambda: left.directory == tree)
        await settle(pilot, left)
        assert left.filter_text == ""
        assert left.row_count == len(left.visible_entries)
        assert "alpha.txt" in plain_names(left)


@run_async
async def test_cursor_movement_does_not_clear_the_filter(tree: Path, tmp_path: Path) -> None:
    """AC3: moving the cursor is not a directory change and keeps the filter."""
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await pilot.press("slash")
        await pilot.press("a")
        await wait_for(pilot, lambda: left.row_count == 3)
        assert left.filter_text == "a"
        await pilot.press("enter")  # confirm the filter, focus back on the table
        await pilot.press("down")
        await pilot.pause()
        assert left.directory == tree
        assert left.filter_text == "a"
        assert left.row_count == 3


@run_async
async def test_filter_starts_fresh_after_navigation_cleared_it(
    tree: Path, tmp_path: Path
) -> None:
    """AC4: after the reset a new ``/`` filter is empty, not the old text."""
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await pilot.press("slash")
        await pilot.press(*"alpha")
        await wait_for(pilot, lambda: left.row_count == 1)
        await pilot.press("enter")  # confirm the filter, hide the input
        left.go_to(tree / "docs")
        await settle(pilot, left)
        assert left.filter_text == ""
        assert page.filter_input.value == ""
        await pilot.press("slash")
        assert page.filter_input.value == "", "the old filter text was kept"
        await pilot.press(*"deep")
        await wait_for(pilot, lambda: left.row_count == 1)
        assert plain_names(left) == ["deep.md"]


@run_async
async def test_navigation_resets_only_the_navigating_panels_filter(
    tree: Path, tmp_path: Path
) -> None:
    """The reset must not touch the other pane's own filter state."""
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left, right = page.panels
        await settle(pilot, left)
        left.set_filter("alpha")
        right.set_filter("beta")
        await pilot.pause()
        left.go_to(tree / "docs")
        await settle(pilot, left)
        assert left.filter_text == ""
        assert right.filter_text == "beta"



@run_async
async def test_typing_more_never_accumulates_rows(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await pilot.press("slash")
        for character in "alpha":
            await pilot.press(character)
            await pilot.pause()
        await wait_for(pilot, lambda: left.filter_text == "alpha")
        assert left.row_count == len(left.visible_entries)


@run_async
async def test_sort_cycles_through_name_size_time_type(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        assert left.sort_key == "name"
        for expected in ("size", "time", "type", "name"):
            await pilot.press("s")
            await pilot.pause()
            assert left.sort_key == expected


@run_async
async def test_sort_by_size_reorders_rows(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await pilot.press("s")
        await wait_for(pilot, lambda: left.sort_key == "size" and not left.loading)
        assert plain_names(left)[0] == ".."
        sizes = [entry.size for entry in left.visible_entries if not entry.is_dir]
        assert sizes == sorted(sizes)


@run_async
async def test_sort_by_type_reorders_rows(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await pilot.press("s", "s", "s")
        await wait_for(pilot, lambda: left.sort_key == "type" and not left.loading)
        labels = [entry.suffix for entry in left.visible_entries if not entry.is_dir]
        assert labels == sorted(labels)


@run_async
async def test_reverse_sort_flips_the_order(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        before = plain_names(left)
        await pilot.press("r")
        await wait_for(pilot, lambda: left.sort_reverse and not left.loading)
        assert plain_names(left)[0] == ".."
        assert plain_names(left)[1:] == before[:0:-1]


# -- AC6: hidden toggle ----------------------------------------------------


@run_async
async def test_hidden_entries_are_hidden_by_default(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        assert ".hidden" not in plain_names(left)


@run_async
async def test_toggle_hidden_shows_dotfiles(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await pilot.press("ctrl+h")
        await wait_for(pilot, lambda: ".hidden" in plain_names(left))
        assert app.file_manager.show_hidden is True


@run_async
async def test_toggle_hidden_persists_via_config_manager(tree: Path, tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    app = make_app(tree, config_path)
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page.panel("left"))
        await pilot.press("ctrl+h")
        await wait_for(pilot, lambda: ConfigManager(config_path).load().show_hidden is True)
    assert ConfigManager(config_path).load().show_hidden is True


@run_async
async def test_toggle_hidden_twice_returns_to_the_original_listing(
    tree: Path, tmp_path: Path
) -> None:
    # The config file must live outside ``tree``: persisting the preference would
    # otherwise add a new entry to the very listing this test compares.
    config_path = Path(tempfile.gettempdir()) / "daylight-dc002-page-config.json"
    app = make_app(tree, config_path)
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        before = plain_names(left)
        await pilot.press("ctrl+h")
        await wait_for(pilot, lambda: ".hidden" in plain_names(left))
        await pilot.press("ctrl+h")
        await wait_for(pilot, lambda: ".hidden" not in plain_names(left))
        assert plain_names(left) == before


@run_async
async def test_starting_with_show_hidden_true_lists_dotfiles(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json", show_hidden=True)
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        assert ".hidden" in plain_names(left)


@run_async
async def test_a_persisted_hidden_toggle_is_restored_on_the_next_run(
    tree: Path, tmp_path: Path
) -> None:
    """AC6: the preference a run saves is the one the next run lists with."""
    # The config must live outside ``tree``: it is written to while the listing
    # under test is being compared against.
    config_path = Path(tempfile.gettempdir()) / "daylight-dc002-restore-config.json"
    first = make_app(tree, config_path)
    async with first.run_test(size=SIZE) as pilot:
        page = first.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        assert ".hidden" not in plain_names(left)
        await pilot.press("ctrl+h")
        await wait_for(pilot, lambda: ".hidden" in plain_names(left))

    second = DaylightApp(config_path=config_path)
    second.start_path = tree
    async with second.run_test(size=SIZE) as pilot:
        page = second.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        assert second.app_config.show_hidden is True
        assert second.file_manager.show_hidden is True
        assert ".hidden" in plain_names(left)


# -- AC1: the listing is virtualised, not materialised ----------------------

#: The listing size the AC names. A full materialisation of this many rows is
#: what the reviewer measured at 11-24 s, so the gate uses the same number.
LARGE = 100_000

#: Longest gap tolerated between two turns of the event loop while listing.
#: A full materialisation stalled for hundreds of milliseconds per batch and
#: made keypresses take seconds, so this threshold cannot be met by accident.
MAX_STALL = 0.5

#: Longest tolerated keypress on a 100k listing. A full materialisation needed
#: seconds; the virtual window answers in well under a tenth of that.
MAX_KEYPRESS = 0.5

#: Longest tolerated gap while a *real* 100k directory is scanned and sorted on
#: two panels at once. Measured at ~0.12 s, so this leaves room for a loaded
#: machine while staying far below the ~0.43 s a full materialisation needed.
MAX_REAL_STALL = 0.35

#: Long file names, on purpose: they push the Name column past the pane width,
#: which gives the table a horizontal scrollbar, which costs a row of viewport.
#: A window sized from the raw table height would then be one row too tall and
#: the table would scroll itself out of step with the panel.
LONG_NAME = "entry_{:06d}.dat"


def make_big_dir(root: Path, count: int) -> Path:
    """Create a real ``count``-entry directory, the reviewer's AC1 scenario."""
    big = root / "big"
    big.mkdir()
    for index in range(count):
        (big / LONG_NAME.format(index)).write_bytes(b"")
    return big


async def fill_panels(pilot: Pilot, panels: tuple[FilePanel, ...], turns: int = 6000) -> None:
    """Wait until every panel finished listing."""
    for _ in range(turns):
        if all(not panel.loading and panel.total_count for panel in panels):
            return
        await pilot.pause()
    raise AssertionError("the large listing never arrived")


async def load_large(panel: FilePanel, pilot: Pilot, count: int = LARGE) -> None:
    """Give ``panel`` a synthetic ``count``-entry listing and wait for it to show."""
    panel.entries = [DirEntry.parent(panel.directory), *(fake_entry(i) for i in range(count))]
    panel.refresh_view()
    for _ in range(SETTLE * 40):
        if not panel.loading and panel.total_count == count + 1:
            return
        await pilot.pause()
    raise AssertionError("the large listing never arrived")


async def wheel(panel: FilePanel, pilot: Pilot, *, down: bool) -> None:
    """Post one mouse-wheel notch over the panel's table."""
    event = MouseScrollDown if down else MouseScrollUp
    delta = 1 if down else -1
    panel.table.post_message(
        event(
            widget=panel.table,
            x=1,
            y=1,
            delta_x=0,
            delta_y=delta,
            button=0,
            shift=False,
            meta=False,
            ctrl=False,
        )
    )
    await pilot.pause()


@run_async
async def test_100k_entries_materialise_only_the_rows_that_fit(tree: Path, tmp_path: Path) -> None:
    """A 100k listing must leave the table bounded and the event loop turning."""
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        ticks = 0
        worst = 0.0
        last = time.perf_counter()

        async def tick() -> None:
            nonlocal ticks, worst, last
            while True:
                now = time.perf_counter()
                worst = max(worst, now - last)
                last = now
                ticks += 1
                await asyncio.sleep(0)

        task = asyncio.create_task(tick())
        await load_large(left, pilot)
        task.cancel()
        assert left.total_count == LARGE + 1
        assert left.row_count == left.viewport_rows < SIZE[1]
        assert left.row_count < 1000, "the whole listing was materialised"
        assert left.table.max_scroll_y == 0, "the table can scroll on its own"
        assert ticks > 1, "the event loop was blocked while listing"
        assert worst < MAX_STALL, f"the event loop stalled for {worst:.3f}s"


@run_async
async def test_a_real_100k_directory_is_listed_without_blocking(tmp_path: Path) -> None:
    """AC1 against the reviewer's own scenario: a real 100k-entry directory.

    The synthetic test above proves the window is bounded; this one proves it
    end to end, with both panels really scanning, filtering and sorting 100k
    entries off the disk. Every assertion below fails on a full materialisation:
    the table would hold all 100001 rows, fill in 11-24 s, stall the loop for
    ~0.43 s at a time and need ~4.6 s per keypress.
    """
    big = make_big_dir(tmp_path, LARGE)
    app = make_app(tmp_path, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left, right = page.panels
        await settle(pilot, left)
        await settle(pilot, right)
        worst = 0.0
        last = time.perf_counter()

        async def tick() -> None:
            nonlocal worst, last
            while True:
                now = time.perf_counter()
                worst = max(worst, now - last)
                last = now
                await asyncio.sleep(0)

        task = asyncio.create_task(tick())
        left.go_to(big)
        right.go_to(big)
        await fill_panels(pilot, (left, right))
        task.cancel()

        # The listing is complete and the table still holds one viewport.
        assert left.total_count == LARGE + 1, "the real 100k directory was not listed"
        assert right.total_count == LARGE + 1
        assert left.row_count == left.viewport_rows
        assert left.row_count < LARGE // 100, "the whole listing was materialised"
        assert left.table.max_scroll_y == 0, "the table can scroll on its own"
        assert worst < MAX_REAL_STALL, f"the event loop stalled for {worst:.3f}s"

        # The listing stays responsive, and keeps the table in its window.
        worst_key = 0.0
        for _ in range(20):
            started = time.perf_counter()
            await pilot.press("down")
            worst_key = max(worst_key, time.perf_counter() - started)
        assert worst_key < MAX_KEYPRESS, f"a keypress took {worst_key:.3f}s"
        assert left.table.scroll_offset.y == 0

        await pilot.press("end")
        assert left.cursor_index == LARGE
        assert left.selected_entry() is left.visible_entries[-1]
        assert plain_names(left)[left.local_cursor] == left.selected_entry().name
        assert left.table.max_scroll_y == 0
        assert left.table.scroll_offset.y == 0, "the table drifted out of the window"


@run_async
async def test_the_table_never_scrolls_on_its_own(tree: Path, tmp_path: Path) -> None:
    """``max_scroll_y == 0`` is what keeps the panel's offset authoritative."""
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await load_large(left, pilot, count=200)
        assert left.row_count < 200
        assert left.table.max_scroll_y == 0


@run_async
async def test_scrolling_slides_the_window_instead_of_growing_the_table(
    tree: Path, tmp_path: Path
) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SMALL) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await load_large(left, pilot, count=200)
        assert left.window_start == 0
        for _ in range(left.viewport_rows + 5):
            await pilot.press("down")
        await pilot.pause()
        assert left.window_start > 0
        assert left.row_count == left.viewport_rows
        assert left.table.max_scroll_y == 0


@run_async
async def test_end_and_home_jump_across_a_virtualised_listing(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SMALL) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await load_large(left, pilot, count=200)
        await pilot.press("end")
        await pilot.pause()
        assert left.cursor_index == left.total_count - 1
        assert left.selected_entry() is left.visible_entries[-1]
        assert left.row_count == left.viewport_rows
        await pilot.press("home")
        await pilot.pause()
        assert left.cursor_index == 0
        assert left.selected_entry() is left.visible_entries[0]


@run_async
async def test_the_cursor_row_always_names_the_selected_entry(tree: Path, tmp_path: Path) -> None:
    """The row under the cursor and the entry it selects must not drift apart."""
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SMALL) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await load_large(left, pilot, count=200)
        for _ in range(30):
            await pilot.press("down")
        await pilot.pause()
        selected = left.selected_entry()
        assert selected is not None
        assert plain_names(left)[left.local_cursor] == selected.name
        await pilot.press("up")
        await pilot.pause()
        assert plain_names(left)[left.local_cursor] == left.selected_entry().name


@run_async
async def test_page_down_and_page_up_move_a_whole_viewport(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SMALL) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await load_large(left, pilot, count=200)
        await pilot.press("pagedown")
        await pilot.pause()
        assert left.cursor_index == left.viewport_rows
        assert left.row_count == left.viewport_rows
        await pilot.press("pageup")
        await pilot.pause()
        assert left.cursor_index == 0


@run_async
async def test_the_mouse_wheel_scrolls_the_virtual_viewport(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SMALL) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await load_large(left, pilot, count=200)
        await wheel(left, pilot, down=True)
        assert left.window_start == SCROLL_STEP
        assert left.row_count == left.viewport_rows
        await wheel(left, pilot, down=False)
        assert left.window_start == 0
        assert left.table.scroll_offset.y == 0


@run_async
async def test_the_breadcrumb_reports_the_window_and_the_listing_size(
    tree: Path, tmp_path: Path
) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SMALL) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        assert left.breadcrumb_text().endswith(f"{left.total_count} items")
        await load_large(left, pilot, count=200)
        assert f"1-{left.window_stop} of 201 items" in left.breadcrumb_text()


@run_async
async def test_resizing_rebuilds_the_window_for_the_new_viewport(
    tree: Path, tmp_path: Path
) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SMALL) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await load_large(left, pilot, count=200)
        before = left.viewport_rows
        await pilot.resize_terminal(*SIZE)
        await wait_for(pilot, lambda: left.viewport_rows > before)
        assert left.row_count == left.viewport_rows
        assert left.table.max_scroll_y == 0


@run_async
async def test_enter_navigates_into_a_directory_found_by_scrolling(
    tmp_path: Path,
) -> None:
    """The virtual cursor has to map back to the right directory after a scroll."""
    root = tmp_path / "many"
    root.mkdir()
    for index in range(40):
        (root / f"d{index:02d}").mkdir()
    app = make_app(root, Path(tempfile.gettempdir()) / "daylight-dc002-scroll-config.json")
    async with app.run_test(size=SMALL) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        assert left.total_count == 41
        assert left.row_count < 41
        for _ in range(20):
            await pilot.press("down")
        await pilot.pause()
        target = left.selected_entry()
        assert target is not None and target.is_dir
        assert plain_names(left)[left.local_cursor] == target.name
        await pilot.press("enter")
        await wait_for(pilot, lambda: left.directory == target.path)
        assert left.directory == target.path


@run_async
async def test_scan_runs_off_the_event_loop(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The directory read must happen in a worker thread, not on the loop."""
    threads: list[str] = []
    original = FileManager.iter_entries

    def spy(manager: FileManager, path: Any) -> Iterator[DirEntry]:
        threads.append(threading.current_thread().name)
        return original(manager, path)

    monkeypatch.setattr(FileManager, "iter_entries", spy)
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page.panel("left"))
    assert threads, "the directory was never scanned"
    assert all(
        name != threading.main_thread().name for name in threads
    ), f"the scan ran on the event loop: {threads}"


@run_async
async def test_filtering_a_large_listing_keeps_the_table_bounded(
    tree: Path, tmp_path: Path
) -> None:
    """The filter path rebuilds a window too, it must not re-fill the table."""
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await load_large(left, pilot, count=500)
        await pilot.press("slash")
        await pilot.press(*"f000042")
        await wait_for(pilot, lambda: left.total_count == 1)
        assert left.row_count == 1
        assert plain_names(left) == ["f000042.dat"]
        assert left.table.max_scroll_y == 0


# -- wiring ----------------------------------------------------------------


@run_async
async def test_page_is_composed_by_the_app(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE):
        page = app.query_one(FilesPage)
        assert list(PANEL_NAMES) == ["left", "right"]
        assert app.query_one("#panel-left")
        assert app.query_one("#panel-right")
        assert page.query_one(Input).id == "files-filter"


@run_async
async def test_page_uses_the_apps_file_manager(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page.panel("left"))
        assert page.file_manager is app.file_manager
        assert page.config_manager is app.config_manager


@run_async
async def test_row_width_matches_the_builder_columns(tree: Path, tmp_path: Path) -> None:
    from daylight.components import file_table

    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        assert len(left.table.columns) == len(file_table.COLUMNS)
        assert len(left.table.get_row_at(0)) == len(file_table.COLUMNS)


@run_async
async def test_rows_show_size_time_permissions_and_icon(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        index = plain_names(left).index("beta.py")
        name, size, modified, permissions = left.table.get_row_at(index)
        assert name.endswith("beta.py")
        assert size == "3.0K"
        assert len(modified) == 16
        assert permissions == "rw-r--r--"
