"""Stats page tests: reachability, cards, charts, cache, refresh (DC-023).

The pilot scenarios drive the real app; the chart checks assert the plain data
the charts were handed, and one snapshot pins the whole dashboard. Fixture
mtimes are pinned so timelines and insights render deterministically.
"""

from __future__ import annotations

import asyncio
import functools
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any, TypeVar

import pytest
from textual.pilot import Pilot

from daylight.app import DaylightApp
from daylight.components.modules.file_list import FilesPage
from daylight.components.modules.stats_page import StatsPage
from daylight.components.stats_charts import BarChart, CalendarHeatmap
from daylight.config import DEFAULT_KEYBINDINGS
from daylight.managers import ConfigManager, FileManager
from daylight.managers.stats import build_stats
from daylight.models import AppConfig

#: Generous ceiling: worker threads need a few turns to finish a scan.
SETTLE = 120

#: Terminal size used for every pilot scenario.
SIZE = (120, 40)

#: Pinned modification time (2026-03-04 05:06:07 local) for the fixture trees.
MTIME = 1_772_601_567

#: Fixed, checkout-independent directory for the snapshot fixture.
SNAPSHOT_FIXTURE = Path(tempfile.gettempdir()) / "daylight-dc023-snapshot"

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def run_async(fn: F) -> F:
    """Run an async test body on a fresh event loop (no pytest-asyncio needed)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


def build_tree(root: Path) -> Path:
    """A small tree with known sizes: 4 files, 1 directory, 3172 bytes."""
    root.mkdir(parents=True, exist_ok=True)
    for name, size in {"readme.md": 100, "main.py": 1000, "photo.jpg": 2048}.items():
        target = root / name
        target.write_text("x" * size, encoding="utf-8")
        os.utime(target, (MTIME, MTIME))
    (root / "sub").mkdir(exist_ok=True)
    (root / "sub" / "deep.py").write_text("d" * 24, encoding="utf-8")
    os.utime(root / "sub" / "deep.py", (MTIME, MTIME))
    return root


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return build_tree(tmp_path / "stats-tree")


@pytest.fixture
def config_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A config file of this test's own, so no run inherits another's choices."""
    return tmp_path_factory.mktemp("dc023") / "config.json"


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


async def wait_for_stats(pilot: Pilot) -> StatsPage:
    """Block until the Stats page finished its first scan."""
    app = pilot.app
    assert isinstance(app, DaylightApp)
    for _ in range(SETTLE):
        page = app._pages.get("stats")
        if isinstance(page, StatsPage) and page.stats is not None:
            return page
        await pilot.pause()
    raise AssertionError("the Stats page did not finish its scan")


async def wait_for(pilot: Pilot, condition: Callable[[DaylightApp], bool]) -> None:
    """Block until ``condition`` holds for the app."""
    for _ in range(SETTLE):
        if condition(pilot.app):  # type: ignore[arg-type]
            return
        await pilot.pause()
    raise AssertionError("the app did not reach the expected state")


# -- AC1: the fifth tab is reachable -----------------------------------------


@run_async
async def test_ctrl5_opens_the_stats_tab(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+5")
        await wait_for(pilot, lambda app: app.active_page == "stats")
        assert app.tabs.active == "stats"
        page = await wait_for_stats(pilot)
        assert page.display is True
        assert page.current_directory == tree
        assert str(tree) in page.title_text
        files = app.query_one(FilesPage)
        assert files.display is False


@run_async
async def test_the_stats_tab_is_reachable_by_clicking_it(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.tabs.active = "stats"
        await wait_for(pilot, lambda app: app.active_page == "stats")
        assert app.query_one(StatsPage).display is True


def test_the_palette_offers_go_to_stats() -> None:
    assert DEFAULT_KEYBINDINGS["goto_stats"] == "ctrl+5"
    names = {command.name for command in make_app().palette_commands()}
    assert "Go to Stats" in names


@run_async
async def test_the_palette_command_opens_the_stats_tab(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.run_page_action("stats", "refresh_stats")
        await wait_for(pilot, lambda app: app.active_page == "stats")
        await wait_for_stats(pilot)


# -- AC2: summary cards --------------------------------------------------------


@run_async
async def test_the_summary_cards_show_the_fixture_numbers(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("ctrl+5")
        page = await wait_for_stats(pilot)
        assert page.card("Total size").card_value == "3.1 KiB"
        assert page.card("Files").card_value == "4"
        assert page.card("Directories").card_value == "1"
        assert page.card("Dirs : files").card_value == "1:4"
        assert page.card("Hidden").card_value == "0"
        assert page.card("Symlinks").card_value == "0"


# -- AC3: the charts ----------------------------------------------------------


@run_async
async def test_the_charts_render_the_fixture(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("ctrl+5")
        page = await wait_for_stats(pilot)
        stats = page.stats
        assert stats is not None
        assert page._share.parts == [("Code", 1024), ("Documents", 100), ("Images", 2048)]
        assert page._extensions.rows == [(".jpg", 2048), (".py", 1024), (".md", 100)]
        assert page._histogram.columns[1] == ("<1K", 3)
        assert page._histogram.columns[2] == ("1-10K", 1)
        assert page._permissions.rows == [("rw-r--r--", 4)]
        month = date.fromtimestamp(MTIME).strftime("%Y-%m")
        assert page._monthly.rows == [(month, 4)]
        assert page._heatmap.counts[date.fromtimestamp(MTIME)] == 4
        assert page._heatmap.anchor == date.fromtimestamp(MTIME)


def test_the_heatmap_anchors_on_the_newest_activity() -> None:
    """A tree last touched long ago shows its own active weeks, not an empty grid."""
    chart = CalendarHeatmap()
    day = date(2026, 3, 4)
    chart.set_days({day: 3, day - timedelta(days=1): 1})
    assert chart.anchor == day
    assert chart.counts[day] == 3


def test_the_heatmap_with_no_data_anchors_on_today() -> None:
    chart = CalendarHeatmap()
    chart.set_days({})
    assert chart.anchor == date.today()


def test_a_bar_chart_keeps_its_rows() -> None:
    chart = BarChart()
    chart.set_bars([(".py", 10), (".md", 5)])
    assert chart.rows == [(".py", 10), (".md", 5)]


# -- AC5: partial scans -------------------------------------------------------


@run_async
async def test_a_partial_scan_marks_the_title_and_the_cards(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("ctrl+5")
        page = await wait_for_stats(pilot)
        page._draw(build_stats(tree, max_entries=1))
        await pilot.pause()
        assert "(partial)" in page.title_text
        assert "(partial)" in page.card("Files").card_detail
        assert "(partial)" in page.card("Total size").card_detail


# -- AC6: refresh triggers and the per-directory cache --------------------------


@run_async
async def test_the_dashboard_follows_a_directory_change_while_visible(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+5")
        await wait_for_stats(pilot)
        app.query_one(FilesPage).active.go_to(tree / "sub")
        await wait_for(pilot, lambda app: app.current_path() == tree / "sub")
        page = app.query_one(StatsPage)
        await wait_for(
            pilot, lambda app: page.stats is not None and page.stats.root == tree / "sub"
        )
        assert page.stats is not None
        assert page.stats.file_count == 1
        assert page.card("Files").card_value == "1"


@run_async
async def test_ctrl_r_recomputes_bypassing_the_cache(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("ctrl+5")
        page = await wait_for_stats(pilot)
        assert page.stats is not None and page.stats.file_count == 4
        (tree / "later.txt").write_text("more", encoding="utf-8")
        await pilot.press("ctrl+r")
        await wait_for(pilot, lambda app: page.stats is not None and page.stats.file_count == 5)
        assert page.stats is not None and page.stats.root == tree


@run_async
async def test_results_are_cached_per_directory(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+5")
        await wait_for_stats(pilot)
        app.query_one(FilesPage).active.go_to(tree / "sub")
        await wait_for(pilot, lambda app: app.current_path() == tree / "sub")
        page = app.query_one(StatsPage)
        await wait_for(
            pilot, lambda app: page.stats is not None and page.stats.root == tree / "sub"
        )
        assert set(page._cache) == {tree, tree / "sub"}
        # A cached directory renders synchronously, without a worker round trip.
        page.refresh_stats(tree)
        assert page.stats is not None and page.stats.root == tree


@run_async
async def test_a_hidden_stats_tab_refreshes_on_the_next_show(tree: Path) -> None:
    """While another tab is up the dashboard waits; showing it recomputes."""
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.query_one(FilesPage).active.go_to(tree / "sub")
        await wait_for(pilot, lambda app: app.current_path() == tree / "sub")
        await pilot.press("ctrl+5")
        page = await wait_for_stats(pilot)
        assert page.stats is not None
        assert page.stats.root == tree / "sub"
        assert page.stats.file_count == 1


# -- the snapshot ---------------------------------------------------------------


def build_snapshot_fixture() -> Path:
    """The deterministic dashboard tree: fixed path, pinned mtimes."""
    if SNAPSHOT_FIXTURE.exists():
        shutil.rmtree(SNAPSHOT_FIXTURE)
    return build_tree(SNAPSHOT_FIXTURE)


def make_snapshot_app() -> DaylightApp:
    """An app pinned to the snapshot fixture with an isolated config file."""
    app = DaylightApp(AppConfig())
    app.start_path = SNAPSHOT_FIXTURE
    app.config_manager = ConfigManager(Path(tempfile.gettempdir()) / "daylight-dc023-config.json")
    app.file_manager = FileManager()
    return app


async def open_stats(pilot: Pilot) -> None:
    """List the fixture, open the Stats tab and wait for the scan."""
    await wait_for_listing(pilot)
    await pilot.press("ctrl+5")
    await wait_for_stats(pilot)


def test_stats_dashboard_snapshot(snap_compare: Callable[..., bool]) -> None:
    """The whole dashboard: cards, share bar, extensions, heatmap, histogram."""
    build_snapshot_fixture()
    assert snap_compare(make_snapshot_app(), terminal_size=SIZE, run_before=open_stats)
