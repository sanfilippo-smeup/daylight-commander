"""File types tab tests: reachability, cards, chart, cache, refresh (DC-034).

The ranking rules are pure; the pilot scenarios drive the real app against a
temp tree, so the bar values never depend on the developer's home directory.
"""

from __future__ import annotations

import asyncio
import functools
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from textual.pilot import Pilot

from daylight.app import DaylightApp
from daylight.components.modules.file_list import FilesPage
from daylight.components.modules.filetypes_page import FileTypesPage
from daylight.config import DEFAULT_KEYBINDINGS
from daylight.managers import ConfigManager, FileManager
from daylight.managers.filetypes import TOP_FILE_TYPES, file_type_rows
from daylight.managers.stats import build_stats

#: Generous ceiling: worker threads need a few turns to finish a scan.
SETTLE = 120

#: Terminal size used for every pilot scenario.
SIZE = (120, 40)

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def run_async(fn: F) -> F:
    """Run an async test body on a fresh event loop (no pytest-asyncio needed)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


def build_tree(root: Path) -> Path:
    """Three Python files, one Markdown file, one extension-less file, one folder."""
    root.mkdir(parents=True)
    for name in ("a.py", "b.py", "c.py", "notes.md", "README"):
        (root / name).write_text("x", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "nested.py").write_text("x", encoding="utf-8")
    return root


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return build_tree(tmp_path / "filetypes-tree")


@pytest.fixture
def config_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A config file of this test's own, so no run inherits another's choices."""
    return tmp_path_factory.mktemp("dc034") / "config.json"


def make_app(root: Path | None = None, config: Path | None = None) -> DaylightApp:
    """An app reading ``config`` and browsing ``root``."""
    if config is None:
        config = Path(tempfile.mkdtemp()) / "config.json"
    app = DaylightApp(config_path=config)
    app.config_manager = ConfigManager(config)
    app.file_manager = FileManager()
    if root is not None:
        app.start_path = root
    return app


async def wait_for_listing(pilot: Pilot) -> FilesPage:
    """Block until the Files page finished its first listing."""
    app = pilot.app
    assert isinstance(app, DaylightApp)
    for _ in range(SETTLE):
        page = app.files_page
        if page is not None and page.is_ready:
            if all(not panel.loading and panel.row_count for panel in page.panels):
                return page
        await pilot.pause()
    raise AssertionError("the Files page did not finish listing")


async def wait_for_filetypes(pilot: Pilot) -> FileTypesPage:
    """Block until the File types page finished its first scan."""
    app = pilot.app
    assert isinstance(app, DaylightApp)
    for _ in range(SETTLE):
        page = app._pages.get("filetypes")
        if isinstance(page, FileTypesPage) and page.stats is not None:
            return page
        await pilot.pause()
    raise AssertionError("the File types page did not finish its scan")


async def wait_for(pilot: Pilot, condition: Callable[[DaylightApp], bool]) -> None:
    """Block until ``condition`` holds for the app."""
    for _ in range(SETTLE):
        if condition(pilot.app):  # type: ignore[arg-type]
            return
        await pilot.pause()
    raise AssertionError("the app did not reach the expected state")


# -- ranking -----------------------------------------------------------------


def test_rows_rank_by_count_then_label() -> None:
    """Most frequent first, ties alphabetical, extensionless labelled."""
    rows = file_type_rows({"py": 3, "md": 5, "json": 1}, extensionless=2)
    assert rows == [(".md", 5), (".py", 3), ("(none)", 2), (".json", 1)]


def test_rows_respect_the_limit_and_drop_zero_counts() -> None:
    """Empty extensions do not clutter the chart and ``limit`` caps the rows."""
    rows = file_type_rows({"py": 2, "md": 0, "json": 1, "txt": 1}, limit=2)
    assert rows == [(".py", 2), (".json", 1)]
    assert TOP_FILE_TYPES >= 8


# -- reachability ------------------------------------------------------------


@run_async
async def test_ctrl7_opens_the_filetypes_tab(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+7")
        await wait_for(pilot, lambda app: app.active_page == "filetypes")
        assert app.tabs.active == "filetypes"
        page = await wait_for_filetypes(pilot)
        assert page.display is True
        assert page.current_directory == tree
        assert str(tree) in page.title_text
        assert app.query_one(FilesPage).display is False


@run_async
async def test_the_filetypes_tab_is_reachable_by_clicking_it(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.tabs.active = "filetypes"
        await wait_for(pilot, lambda app: app.active_page == "filetypes")
        assert app.query_one(FileTypesPage).display is True


def test_the_palette_offers_go_to_filetypes() -> None:
    assert DEFAULT_KEYBINDINGS["goto_filetypes"] == "ctrl+7"
    names = {command.name for command in make_app().palette_commands()}
    assert "Go to File types" in names


# -- cards and chart ---------------------------------------------------------


@run_async
async def test_the_cards_and_chart_show_the_fixture(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("ctrl+7")
        page = await wait_for_filetypes(pilot)
        assert page.card("Files").card_value == "6"
        assert page.card("File types").card_value == "2"
        assert page.card("File types").card_detail.startswith("with an extension")
        assert page.card("Scan time").card_value.endswith("s")
        assert page.chart.rows == [(".py", 4), ("(none)", 1), (".md", 1)]
        assert "6 files, 2 extensions" in page.title_text


# -- partial scans -----------------------------------------------------------


@run_async
async def test_a_partial_scan_marks_the_title_and_the_cards(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("ctrl+7")
        page = await wait_for_filetypes(pilot)
        page._draw(build_stats(tree, max_entries=1))
        await pilot.pause()
        assert "(partial)" in page.title_text
        assert "(partial)" in page.card("Files").card_detail
        assert "(partial)" in page.card("Scan time").card_detail


# -- refresh triggers and the per-directory cache ----------------------------


@run_async
async def test_the_page_follows_a_directory_change_while_visible(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+7")
        await wait_for_filetypes(pilot)
        app.query_one(FilesPage).active.go_to(tree / "sub")
        await wait_for(pilot, lambda app: app.current_path() == tree / "sub")
        page = app.query_one(FileTypesPage)
        await wait_for(
            pilot, lambda app: page.stats is not None and page.stats.root == tree / "sub"
        )
        assert page.chart.rows == [(".py", 1)]
        assert page.card("Files").card_value == "1"


@run_async
async def test_ctrl_r_recomputes_bypassing_the_cache(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("ctrl+7")
        page = await wait_for_filetypes(pilot)
        assert page.stats is not None and page.stats.file_count == 6
        (tree / "later.json").write_text("x", encoding="utf-8")
        await pilot.press("ctrl+r")
        await wait_for(pilot, lambda app: page.stats is not None and page.stats.file_count == 7)
        assert (".json", 1) in page.chart.rows


# -- registration ------------------------------------------------------------


def test_the_page_is_registered_as_the_seventh_tab() -> None:
    from daylight.app import PAGE_LABELS

    assert PAGE_LABELS["filetypes"] == "File types"
    assert tuple(PAGE_LABELS)[-1] == "filetypes"
    assert DaylightApp.PAGES["filetypes"] is FileTypesPage
