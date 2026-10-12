"""Type-ahead prefix navigation tests (DC-007, FR-109).

The pure prefix search, name ordering and buffer logic is exercised directly,
and the widget wiring is driven through the real app and Textual's pilot with
real letter keypresses. The listing is sorted by size in the alphabet-
fallback tests, so they fail if the fallback follows display order instead of
alphabetical name order (the DC-007 attempt-1 LOW finding).
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from textual.pilot import Pilot
from textual.widgets import Input

from daylight.app import DaylightApp
from daylight.components import file_table
from daylight.components.modules.file_list import FilesPage, TypeAhead
from daylight.managers import ConfigManager, FileManager
from daylight.models import AppConfig, DirEntry

#: Generous ceiling for worker-thread listings and window rebuilds.
SETTLE = 60

#: Terminal size used for every pilot scenario.
SIZE = (120, 40)

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

#: The alphabetically-named entries the acceptance criteria name.
NAMES = ["alpha", "apple", "banana", "cherry"]


def run_async(fn: F) -> F:
    """Run an async test body on a fresh event loop (no pytest-asyncio needed)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A directory holding the acceptance-criteria entries plus a folder.

    Sizes are distinct and deliberately disagree with name order, so a listing
    sorted by size has its rows in a different order than their names.
    """
    files = {
        "alpha": 40,
        "apple": 10,
        "banana": 30,
        "cherry": 20,
        "delta": 50,
        "romeo": 60,
        "sierra": 70,
    }
    for name, size in files.items():
        (tmp_path / name).write_text("x" * size, encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "apple").write_text("", encoding="utf-8")
    (tmp_path / "docs" / "avocado").write_text("", encoding="utf-8")
    return tmp_path


def make_app(tree: Path, config_path: Path) -> DaylightApp:
    """Build an app pinned to ``tree`` with an isolated config file."""
    app = DaylightApp(AppConfig())
    app.start_path = tree
    app.config_manager = ConfigManager(config_path)
    app.file_manager = FileManager()
    return app


def entry_named(name: str) -> DirEntry:
    """A synthetic file row, enough for the pure ordering helper."""
    return DirEntry(
        name=name,
        path=Path("/nowhere") / name,
        is_dir=False,
        is_hidden=False,
        is_symlink=False,
        size=0,
        modified=0.0,
        permissions="rw-r--r--",
        kind="file",
        suffix="",
    )


async def settle(pilot: Pilot, panel: Any) -> None:
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


async def press(pilot: Pilot, panel: Any, *keys: str) -> None:
    """Press ``keys`` and let the cursor move settle."""
    for key in keys:
        await pilot.press(key)
        await pilot.pause()


def selected(panel: Any) -> str:
    """The name under the cursor, asserting there is one."""
    entry = panel.selected_entry()
    assert entry is not None, "the cursor is not on a row"
    return entry.name


# -- pure prefix search (file_table) ---------------------------------------


def test_prefix_match_index_finds_the_first_match() -> None:
    assert file_table.prefix_match_index(["alpha", "apple", "banana"], "a") == 0


def test_prefix_match_index_respects_the_start_index() -> None:
    assert file_table.prefix_match_index(["alpha", "apple"], "a", 1) == 1


def test_prefix_match_index_is_case_insensitive() -> None:
    assert file_table.prefix_match_index(["Alpha"], "a") == 0


def test_prefix_match_index_returns_none_without_a_match() -> None:
    assert file_table.prefix_match_index(["banana", "cherry"], "z") is None


def test_prefix_match_index_ignores_an_empty_prefix() -> None:
    assert file_table.prefix_match_index(["alpha"], "") is None


# -- pure name ordering (file_table) ---------------------------------------


def test_name_order_sorts_display_indices_by_name() -> None:
    entries = [entry_named(name) for name in ["banana", "apple", "cherry", "Alpha"]]
    assert file_table.name_order(entries) == [3, 1, 0, 2]


# -- the type-ahead buffer --------------------------------------------------


def test_first_keystroke_finds_the_first_prefix_match() -> None:
    tracker = TypeAhead()
    assert tracker.advance("a", NAMES, 0.0) == 0


def test_repeated_keystroke_advances_to_the_next_match() -> None:
    tracker = TypeAhead()
    assert tracker.advance("a", NAMES, 0.0) == 0
    assert tracker.advance("a", NAMES, 0.1) == 1


def test_repeated_keystroke_past_the_last_match_falls_through() -> None:
    tracker = TypeAhead()
    tracker.advance("a", NAMES, 0.0)
    tracker.advance("a", NAMES, 0.1)
    assert tracker.advance("a", NAMES, 0.2) is None


def test_different_characters_accumulate_into_a_prefix() -> None:
    tracker = TypeAhead()
    assert tracker.advance("a", NAMES, 0.0) == 0
    assert tracker.advance("p", NAMES, 0.1) == 1


def test_no_match_falls_through_and_resets_the_prefix() -> None:
    tracker = TypeAhead()
    assert tracker.advance("z", NAMES, 0.0) is None
    assert tracker.advance("a", NAMES, 0.1) == 0


def test_matching_is_case_insensitive() -> None:
    tracker = TypeAhead()
    assert tracker.advance("A", NAMES, 0.0) == 0


def test_the_prefix_resets_after_the_idle_timeout() -> None:
    tracker = TypeAhead(timeout=1.0)
    assert tracker.advance("a", NAMES, 0.0) == 0
    assert tracker.advance("b", NAMES, 2.0) == 2


def test_reset_clears_the_accumulated_state() -> None:
    tracker = TypeAhead()
    tracker.advance("a", NAMES, 0.0)
    tracker.reset()
    assert tracker.advance("a", NAMES, 0.1) == 0


# -- letters through the pilot (the DC-007 attempt-1 fix) -------------------


@run_async
async def test_pressing_a_selects_the_first_a_entry(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await press(pilot, left, "a")
        assert selected(left) == "alpha"


@run_async
async def test_pressing_b_selects_banana(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await press(pilot, left, "b")
        assert selected(left) == "banana"


@run_async
async def test_pressing_s_selects_sierra(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await press(pilot, left, "s")
        assert selected(left) == "sierra"


@run_async
async def test_pressing_r_selects_romeo(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await press(pilot, left, "r")
        assert selected(left) == "romeo"


@run_async
async def test_pressing_d_selects_the_first_d_entry(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        # ``delta`` precedes the ``docs`` folder in name order.
        await press(pilot, left, "d")
        assert selected(left) == "delta"


@run_async
async def test_pressing_z_with_no_match_moves_to_the_next_name(
    tree: Path, tmp_path: Path
) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        # The cursor starts on ``..``, whose alphabetical successor is ``alpha``.
        await press(pilot, left, "z")
        assert selected(left) == "alpha"


@run_async
async def test_repeated_letter_advances_through_the_matches(
    tree: Path, tmp_path: Path
) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await press(pilot, left, "a")
        assert selected(left) == "alpha"
        await press(pilot, left, "a")
        assert selected(left) == "apple"


@run_async
async def test_repeated_letter_past_the_last_match_falls_back_alphabetically(
    tree: Path, tmp_path: Path
) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await press(pilot, left, "a", "a", "a")
        assert selected(left) == "banana"


# -- the alphabetical fallback under a non-name sort (LOW fix) ---------------


@run_async
async def test_prefix_match_uses_name_order_under_a_size_sort(
    tree: Path, tmp_path: Path
) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        left.sort_by("size")
        await wait_for(pilot, lambda: left.sort_key == "size" and not left.loading)
        # In listing order the first ``a`` row is ``apple``; by name it is
        # ``alpha``, which is the entry the type-ahead must select.
        await press(pilot, left, "a")
        assert selected(left) == "alpha"


@run_async
async def test_alphabetical_fallback_uses_name_order_under_a_size_sort(
    tree: Path, tmp_path: Path
) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        left.sort_by("size")
        await wait_for(pilot, lambda: left.sort_key == "size" and not left.loading)
        # No second ``b`` matches, so the cursor jumps to the next name, which
        # is ``cherry`` -- not the next row of the size-sorted listing.
        await press(pilot, left, "b", "b")
        assert selected(left) == "cherry"


# -- reset on navigation, reload and idle ----------------------------------


@run_async
async def test_typeahead_prefix_resets_after_navigation(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await press(pilot, left, "a")
        assert selected(left) == "alpha"
        left.go_to(tree / "docs")
        await settle(pilot, left)
        await press(pilot, left, "a")
        assert selected(left) == "apple"


@run_async
async def test_typeahead_prefix_resets_on_reload(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        await press(pilot, left, "a")
        assert selected(left) == "alpha"
        # A copy/delete/refresh calls ``reload``: the half-typed prefix must not
        # survive it, or the next ``a`` would advance to ``apple`` immediately.
        left.reload()
        await settle(pilot, left)
        await press(pilot, left, "a")
        assert selected(left) == "alpha"


# -- does not fire outside the focused listing (AC6) -----------------------


@run_async
async def test_typeahead_does_not_fire_in_the_filter_box(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        before = left.cursor_index
        await pilot.press("slash")
        assert isinstance(app.focused, Input)
        await press(pilot, left, "b")
        assert left.filter_text == "b"
        assert left.cursor_index == before


@run_async
async def test_typeahead_fires_only_in_the_focused_panel(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left, right = page.panels
        await settle(pilot, left)
        await settle(pilot, right)
        await pilot.press("ctrl+t")
        left_before = left.cursor_index
        await press(pilot, right, "b")
        assert left.cursor_index == left_before
        assert selected(right) == "banana"


@run_async
async def test_bound_non_letter_keys_keep_their_meaning(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await settle(pilot, left)
        # Put the cursor on a real entry, then ``space`` still marks it instead
        # of being consumed as type-ahead.
        await press(pilot, left, "s")
        assert selected(left) == "sierra"
        await pilot.press("space")
        await pilot.pause()
        assert left.selected_count == 1
        # ``/`` still opens the filter box.
        await pilot.press("slash")
        assert isinstance(app.focused, Input)
