"""Dashboard page tests: dynamic tab lifecycle, every block kind, refresh (DC-030).

The app-level scenarios drive the real shell (tab creation, reuse, activation);
the block-level scenarios mount a ``DashboardPage`` backed by a synthetic
``stats_provider``, so a fixed :class:`DirectoryStats` is rendered without
touching the disk beyond the fixture listing. Mtimes are pinned so timelines and
table dates stay deterministic.
"""

from __future__ import annotations

import asyncio
import functools
import os
import threading
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

import pytest
from textual.pilot import Pilot

from daylight.app import PAGE_LABELS, DaylightApp
from daylight.components.modules import dashboard_page as dashboard_page_module
from daylight.components.modules.dashboard_page import (
    EMPTY_MESSAGE,
    DashboardPage,
    TableBlock,
)
from daylight.components.modules.file_list import FilesPage
from daylight.components.stats_charts import (
    BarChart,
    CalendarHeatmap,
    Histogram,
    ShareBar,
    SummaryCard,
    human_size,
    insights_rows,
)
from daylight.managers.stats import build_stats
from daylight.models import (
    DashboardBlock,
    DashboardSpec,
    DirectoryStats,
    FileStat,
)

#: Generous ceiling: worker threads need a few turns to finish a scan.
SETTLE = 120

#: Terminal size used for every pilot scenario.
SIZE = (120, 40)

#: Pinned modification time (2026-03-04 05:06:07 local) for the fixture trees.
MTIME = 1_772_601_567

#: The pinned mtime as the heatmap and the insights list see it.
DAY = date.fromtimestamp(MTIME)

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
    return build_tree(tmp_path / "dashboard-tree")


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """A config file of this test's own, so no run inherits another's choices."""
    return tmp_path / "config.json"


def make_app(root: Path | None = None, config: Path | None = None) -> DaylightApp:
    """An app reading ``config`` and browsing ``root``."""
    app = DaylightApp(config_path=config)
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


async def wait_for(pilot: Pilot, condition: Callable[[DaylightApp], bool]) -> None:
    """Block until ``condition`` holds for the app."""
    for _ in range(SETTLE):
        if condition(pilot.app):  # type: ignore[arg-type]
            return
        await pilot.pause()
    raise AssertionError("the app did not reach the expected state")


def fixed_stats(root: Path, *, partial: bool = False, scanned: int = 5) -> DirectoryStats:
    """A synthetic ``DirectoryStats``: every source populated, deterministic.

    ``category_sizes`` carries a zero-valued part the share bar has to drop,
    and ``extension_sizes``/``extension_counts`` use the raw DC-029 key shape.
    """
    return DirectoryStats(
        root=root,
        total_size=4096,
        file_count=5,
        directory_count=1,
        hidden_count=1,
        symlink_count=0,
        scanned_entries=scanned,
        partial=partial,
        elapsed=0.0,
        category_sizes={"Code": 1024, "Documents": 512, "Images": 2048, "Empty": 0},
        extension_sizes={"py": 1000, "md": 100},
        extension_counts={"py": 3, "md": 2},
        size_buckets={"<1K": 4, "1-10K": 1},
        permission_counts={"rw-r--r--": 5},
        daily_counts={DAY: 3},
        monthly_counts={"2026-03": 5},
        largest=(FileStat(path=root / "big.py", name="big.py", size=2048, modified=MTIME),),
        oldest=FileStat(path=root / "old.log", name="old.log", size=10, modified=1_000_000_000),
        newest=FileStat(path=root / "new.txt", name="new.txt", size=32, modified=1_300_000_000),
    )


ALL_KINDS_SPEC = DashboardSpec(
    schema_version=1,
    # delibately markup-shaped: untrusted agent output must show as plain text
    title="Overview [b]x[/b]",
    blocks=(
        DashboardBlock(kind="summary_cards", source="summary"),
        DashboardBlock(
            kind="bar_chart",
            source="extension_sizes",
            title="Extensions by size",
            top_n=1,
            color_token="secondary",
        ),
        DashboardBlock(kind="bar_chart", source="extension_counts"),
        DashboardBlock(kind="share_bar", source="category_sizes", title="Share [red]x[/red]"),
        DashboardBlock(kind="histogram", source="size_buckets"),
        DashboardBlock(kind="heatmap", source="daily_counts"),
        DashboardBlock(kind="table", source="insights"),
    ),
)

#: One bar chart block, for the tab-level app scenarios.
SIMPLE_SPEC = DashboardSpec(
    schema_version=1,
    title="Overview",
    blocks=(
        DashboardBlock(kind="summary_cards", source="summary"),
        DashboardBlock(kind="bar_chart", source="extension_sizes", top_n=2),
    ),
)

#: A different dashboard for the reuse scenario.
SECOND_SPEC = DashboardSpec(
    schema_version=1,
    title="Second",
    blocks=(DashboardBlock(kind="bar_chart", source="extension_counts"),),
)

#: A summary-only dashboard, for partial marking (cards are where it shows).
SUMMARY_SPEC = DashboardSpec(
    schema_version=1,
    title="Overview",
    blocks=(DashboardBlock(kind="summary_cards", source="summary"),),
)


def mounted_page(
    pilot: Pilot,
    provider: Callable[[Path], DirectoryStats],
) -> DashboardPage:
    """A ``DashboardPage`` backed by ``provider``, mounted for the scenario."""
    return DashboardPage(stats_provider=provider)


async def render_fixed_page(
    pilot: Pilot, spec: DashboardSpec = ALL_KINDS_SPEC
) -> tuple[DashboardPage, DirectoryStats]:
    """Mount a dashboard page over a fixed stats provider and wait for it."""
    app = pilot.app
    assert isinstance(app, DaylightApp)
    stats = fixed_stats(Path(app.start_path))
    page = mounted_page(pilot, lambda directory: stats)
    await app.mount(page)
    page.set_dashboard(spec, Path(app.start_path))
    await wait_for(pilot, lambda app: page.stats is not None)
    return page, stats


# -- the dynamic tab (AC1) ------------------------------------------------------


@run_async
async def test_open_dashboard_adds_one_active_tab(tree: Path, config_path: Path) -> None:
    app = make_app(tree, config_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.open_dashboard(SIMPLE_SPEC, tree)
        await wait_for(pilot, lambda app: app.active_page == "dashboard")
        # Tabs.add_tab is an AwaitComplete: the tab exists a beat later (DC-030).
        await wait_for(pilot, lambda app: app.tabs.get_tab("dashboard") is not None)
        await wait_for(pilot, lambda app: app.tabs.active == "dashboard")
        tab = app.tabs.get_tab("dashboard")
        assert tab is not None
        assert str(tab.label) == "Dashboard"
        assert len(app.tabs.query("Tab")) == len(PAGE_LABELS) + 1
        assert app.tabs.active == "dashboard"
        page = app._pages["dashboard"]
        assert isinstance(page, DashboardPage)
        assert page.display is True
        # the default provider computed the real tree it was opened with
        await wait_for(pilot, lambda app: page.stats is not None)
        assert page.stats is not None and page.stats.root == tree
        files = page.summary_card("Files")
        assert files is not None and files.card_value == "4"
        # the standard framing: the page's own Refresh key is on screen
        assert any("Refresh" in description for _, description in app.key_hints())


@run_async
async def test_the_first_open_dashboard_starts_exactly_one_scan(
    monkeypatch: pytest.MonkeyPatch, tree: Path, config_path: Path
) -> None:
    """A first ``open_dashboard`` starts exactly one statistics scan (DC-030 F1).

    The first open builds and mounts the page and binds a spec in the same
    turn: the page is attached while ``on_mount`` has yet to run, and only one
    of the two start paths may fire. The provider is counted after the scan
    has settled, and the request generation -- bumped once per start on the UI
    thread -- leaves a second start no room to hide in thread scheduling.
    """
    app = make_app(tree, config_path)
    calls: list[Path] = []

    def counting_provider(directory: Path) -> DirectoryStats:
        calls.append(Path(directory))
        return fixed_stats(Path(directory))

    # the default provider is a module global, resolved at page construction
    monkeypatch.setattr(dashboard_page_module, "build_stats", counting_provider)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.open_dashboard(SIMPLE_SPEC, tree)
        page = app._pages["dashboard"]
        assert isinstance(page, DashboardPage)
        await wait_for(pilot, lambda app: page.stats is not None)
        await page.workers.wait_for_complete()
        await pilot.pause()
        assert calls == [tree]
        assert page._generation == 1


@run_async
async def test_a_second_open_reuses_the_tab_and_replaces_the_content(
    tree: Path, config_path: Path
) -> None:
    app = make_app(tree, config_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.open_dashboard(SIMPLE_SPEC, tree)
        page = app._pages["dashboard"]
        assert isinstance(page, DashboardPage)
        await wait_for(pilot, lambda app: app.tabs.get_tab("dashboard") is not None)
        await wait_for(pilot, lambda app: page.stats is not None)
        tab_before = app.tabs.get_tab("dashboard")
        tab_count = len(app.tabs.query("Tab"))
        app.open_dashboard(SECOND_SPEC, tree / "sub")
        await wait_for(
            pilot,
            lambda app: page.stats is not None and page.stats.root == tree / "sub",  # noqa: E501
        )
        assert app.tabs.get_tab("dashboard") is tab_before
        assert len(app.tabs.query("Tab")) == tab_count
        assert app._pages["dashboard"] is page
        assert app.tabs.active == "dashboard"
        assert str(app.tabs.get_tab("dashboard").label) == "Dashboard"
        assert page.stats is not None and page.stats.file_count == 1


# -- every block kind (AC2) -----------------------------------------------------


@run_async
async def test_every_block_kind_renders_the_expected_widgets(
    tmp_path: Path, config_path: Path
) -> None:
    app = make_app(tmp_path, config_path)
    async with app.run_test(size=SIZE) as pilot:
        page, stats = await render_fixed_page(pilot)
        # summary_cards: the six DC-029 values, in the Stats page's order
        labels = [card.card_label for card in page.query(SummaryCard)]
        assert labels == [
            "Total size",
            "Files",
            "Directories",
            "Dirs : files",
            "Hidden",
            "Symlinks",
        ]
        total = page.summary_card("Total size")
        assert total is not None
        assert total.card_value == "4.0 KiB"
        assert total.card_detail == "5 files"
        dirs_files = page.summary_card("Dirs : files")
        assert dirs_files is not None and dirs_files.card_value == "1:5"
        # bar_chart: rows capped at top_n, with the requested color token
        charts = list(page.query(BarChart))
        assert len(charts) == 2
        sizes = next(chart for chart in charts if chart._color_token == "secondary")
        counts = next(chart for chart in charts if chart._color_token == "accent")
        assert sizes.rows == [("py", 1000)]
        assert sizes._format_value is human_size
        assert counts.rows == [("py", 3), ("md", 2)]
        assert counts._format_value is str
        # share_bar: the zero-valued part is dropped, as the Stats bar does
        share = page.query_one(ShareBar)
        assert share.parts == [("Images", 2048), ("Code", 1024), ("Documents", 512)]
        # histogram: the bucket order is kept
        histogram = page.query_one(Histogram)
        assert histogram.columns == [("<1K", 4), ("1-10K", 1)]
        # heatmap: the bound days
        heatmap = page.query_one(CalendarHeatmap)
        assert heatmap.counts == {DAY: 3}
        assert heatmap.anchor == DAY
        # table: headers and rows in the Stats insights shape
        table = page.query_one(TableBlock)
        assert table.table.headers == ("#", "Name", "Size", "Modified")
        assert table.table.rows == insights_rows(stats)
        # one framed block widget per block of the spec
        assert len(page.query(".dashboard-block")) == len(ALL_KINDS_SPEC.blocks)


@run_async
async def test_block_titles_show_and_default_to_the_humanized_source(
    tmp_path: Path, config_path: Path
) -> None:
    app = make_app(tmp_path, config_path)
    async with app.run_test(size=SIZE) as pilot:
        page, _stats = await render_fixed_page(pilot)
        sizes = next(chart for chart in page.query(BarChart) if chart._color_token == "secondary")
        assert sizes.border_title == "Extensions by size"
        counts = next(chart for chart in page.query(BarChart) if chart._color_token == "accent")
        # a block without a title shows its source, humanized
        assert counts.border_title == "Extension counts"
        assert page.query_one(Histogram).border_title == "Size buckets"
        assert page.query_one(TableBlock).border_title == "Insights"


@run_async
async def test_spec_text_renders_as_plain_text(tmp_path: Path, config_path: Path) -> None:
    """Untrusted agent text is never parsed as markup (DC-029 F6)."""
    app = make_app(tmp_path, config_path)
    async with app.run_test(size=SIZE) as pilot:
        page, _stats = await render_fixed_page(pilot)
        # the spec title on the page title line, character for character
        assert "Overview [b]x[/b]" in page.title_text
        # a block title carrying tags keeps them as literal text on its border
        share = page.query_one(ShareBar)
        assert "Share" in share.border_title
        assert "\\[red]" in share.border_title


# -- the computing path and refresh (AC4) ----------------------------------------


@run_async
async def test_a_computing_state_precedes_the_first_render(
    tmp_path: Path, config_path: Path
) -> None:
    app = make_app(tmp_path, config_path)
    async with app.run_test(size=SIZE) as pilot:
        gate = threading.Event()

        def provider(directory: Path) -> DirectoryStats:
            gate.wait(5.0)
            return fixed_stats(Path(directory))

        page = mounted_page(pilot, provider)
        await app.mount(page)
        page.set_dashboard(ALL_KINDS_SPEC, tmp_path)
        await pilot.pause()
        assert page.stats is None
        assert "computing…" in page.title_text
        assert page._empty.display is True
        assert len(page.query(".dashboard-block")) == 0
        gate.set()
        await wait_for(pilot, lambda app: page.stats is not None)
        assert len(page.query(".dashboard-block")) == len(ALL_KINDS_SPEC.blocks)
        # the walk happens off the event loop and nothing is left behind
        await page.workers.wait_for_complete()
        assert not list(page.workers)


@run_async
async def test_a_stale_scan_never_overwrites_a_newer_dashboard(
    tmp_path: Path, config_path: Path
) -> None:
    app = make_app(tmp_path, config_path)
    async with app.run_test(size=SIZE) as pilot:
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        gate = threading.Event()
        calls: list[Path] = []

        def provider(directory: Path) -> DirectoryStats:
            path = Path(directory)
            calls.append(path)
            if path == first:
                gate.wait(5.0)
            return fixed_stats(path, scanned=1)

        page = mounted_page(pilot, provider)
        await app.mount(page)
        page.set_dashboard(ALL_KINDS_SPEC, first)
        await wait_for(pilot, lambda app: calls and calls[0] == first)
        page.set_dashboard(ALL_KINDS_SPEC, second)
        await wait_for(pilot, lambda app: page.stats is not None and page.stats.root == second)
        gate.set()
        await page.workers.wait_for_complete()
        await pilot.pause()
        assert page.stats is not None and page.stats.root == second
        assert calls == [first, second]
        assert not list(page.workers)


@run_async
async def test_a_dashboard_bound_before_mount_renders_once_mounted(
    tmp_path: Path, config_path: Path
) -> None:
    app = make_app(tmp_path, config_path)
    async with app.run_test(size=SIZE) as pilot:
        calls: list[Path] = []

        def provider(directory: Path) -> DirectoryStats:
            calls.append(Path(directory))
            return fixed_stats(Path(directory))

        page = mounted_page(pilot, provider)
        page.set_dashboard(SUMMARY_SPEC, tmp_path)  # not mounted yet
        assert calls == []
        await app.mount(page)
        await wait_for(pilot, lambda app: page.stats is not None)
        assert calls == [tmp_path]


@run_async
async def test_ctrl_r_recomputes_the_bound_directory(tree: Path, config_path: Path) -> None:
    app = make_app(tree, config_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.open_dashboard(SIMPLE_SPEC, tree)
        page = app._pages["dashboard"]
        assert isinstance(page, DashboardPage)
        await wait_for(pilot, lambda app: page.stats is not None)
        assert page.stats is not None and page.stats.file_count == 4
        (tree / "later.txt").write_text("more", encoding="utf-8")
        await pilot.press("ctrl+r")
        await wait_for(pilot, lambda app: page.stats is not None and page.stats.file_count == 5)
        assert page.stats is not None and page.stats.root == tree


@run_async
async def test_a_partial_walk_marks_the_title_and_the_cards(
    tmp_path: Path, config_path: Path
) -> None:
    app = make_app(tmp_path, config_path)
    async with app.run_test(size=SIZE) as pilot:
        stats = fixed_stats(tmp_path, partial=True)
        page = mounted_page(pilot, lambda directory: stats)
        await app.mount(page)
        page.set_dashboard(SUMMARY_SPEC, tmp_path)
        await wait_for(pilot, lambda app: page.stats is not None)
        assert "(partial)" in page.title_text
        total = page.summary_card("Total size")
        assert total is not None and total.card_detail == "5 files (partial)"
        files = page.summary_card("Files")
        assert files is not None and files.card_detail == "(partial)"


# -- the palette entry (AC1) -----------------------------------------------------


def test_no_dashboard_palette_entries_before_any_dashboard(tmp_path: Path) -> None:
    app = make_app(tmp_path, tmp_path / "config.json")
    names = {command.name for command in app.palette_commands()}
    assert "Go to Dashboard" not in names
    assert "Dashboard: Refresh dashboard" not in names


@run_async
async def test_the_go_to_dashboard_palette_entry_appears_once_a_dashboard_exists(
    tree: Path, config_path: Path
) -> None:
    app = make_app(tree, config_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.open_dashboard(SIMPLE_SPEC, tree)
        await wait_for(pilot, lambda app: app.tabs.get_tab("dashboard") is not None)
        commands = app.palette_commands()
        names = [command.name for command in commands]
        assert "Go to Dashboard" in names
        assert "Dashboard: Refresh dashboard" in names
        assert len(names) == len(set(names))
        entry = next(command for command in commands if command.name == "Go to Dashboard")
        entry.run()
        await wait_for(pilot, lambda app: app.active_page == "dashboard")


# -- the empty page ---------------------------------------------------------------


@run_async
async def test_an_empty_dashboard_page_shows_the_placeholder(
    tmp_path: Path, config_path: Path
) -> None:
    app = make_app(tmp_path, config_path)
    async with app.run_test(size=SIZE) as pilot:
        page = DashboardPage()
        await app.mount(page)
        page.focus()
        await pilot.pause()
        assert page.stats is None
        assert page.title_text == ""
        assert EMPTY_MESSAGE in str(page._empty.content)
        assert len(page.query(".dashboard-block")) == 0
        await pilot.press("ctrl+r")  # nothing bound: a quiet no-op
        await pilot.pause()
        assert page.stats is None


def test_the_page_defaults_to_build_stats() -> None:
    page = DashboardPage()
    assert page._stats_provider is build_stats
