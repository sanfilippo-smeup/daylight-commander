"""Snapshot tests for the DC-005 shell: header, tabs, hint bar and every tab.

The fixture lives at a fixed path with pinned mtimes, exactly as the DC-002
snapshots do, so the breadcrumb and the Modified column do not depend on where
the repository is checked out.

There is deliberately no Properties snapshot: that view prints the inode, which
changes every time the fixture is rebuilt, so the baseline could never hold.

These snapshots are the metric for the card's ``shell`` axis: they show the whole
app in each tab, so a header that loses the path, a hint bar that stops following
the page, or a tab that covers the listing instead of replacing it fails here.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from textual.pilot import Pilot

from daylight.app import DaylightApp
from daylight.components.modules.file_list import FilesPage
from daylight.managers import ConfigManager, FileManager
from daylight.models import AppConfig

#: Fixed, checkout-independent directory so the breadcrumb is stable.
FIXTURE = Path(tempfile.gettempdir()) / "daylight-dc005-snapshot"

#: Pinned modification time (2026-03-04 05:06:07) for every fixture file.
MTIME = 1_772_601_567

TERMINAL_SIZE = (120, 32)


def build_fixture() -> Path:
    """Create the deterministic tree the shell snapshots browse."""
    if FIXTURE.exists():
        shutil.rmtree(FIXTURE)
    FIXTURE.mkdir(parents=True)
    (FIXTURE / "docs").mkdir()
    (FIXTURE / "notes.md").write_text("# Title\n\nbody\n", encoding="utf-8")
    (FIXTURE / "data.json").write_text('{"a": 1}', encoding="utf-8")
    (FIXTURE / "docs" / "deep.md").write_text("deep\n", encoding="utf-8")
    for target in (FIXTURE / "docs", FIXTURE, *(FIXTURE / name for name in
                   ("notes.md", "data.json", "docs/deep.md"))):
        os.utime(target, (MTIME, MTIME))
    return FIXTURE


def make_app() -> DaylightApp:
    """An app pinned to the fixture with a config file of its own."""
    app = DaylightApp(AppConfig())
    app.start_path = FIXTURE
    app.config_manager = ConfigManager(Path(tempfile.gettempdir()) / "daylight-dc005-config.json")
    app.file_manager = FileManager()
    return app


async def wait_for_listing(pilot: Pilot) -> None:
    """Block until both panels finished their first listing."""
    page = pilot.app.query_one(FilesPage)
    for _ in range(120):
        if all(not panel.loading and panel.row_count for panel in page.panels):
            return
        await pilot.pause()
    raise AssertionError("the Files page did not finish listing")


async def open_a_file_then_wait(pilot: Pilot) -> None:
    """Walk onto ``notes.md`` and open it, so Viewers and Properties have a subject.

    The cursor starts on ``..``; it is walked down onto the file rather than
    assuming a row count, so the snapshot does not depend on the sort order.
    """
    await wait_for_listing(pilot)
    page = pilot.app.query_one(FilesPage)
    for _ in range(120):
        entry = page.active.visible_entries[page.active.table.cursor_row]
        if entry.name == "notes.md":
            break
        await pilot.press("down")
        await pilot.pause()
    else:
        raise AssertionError("notes.md never came under the cursor")
    await pilot.press("v")
    for _ in range(60):
        if pilot.app.view_target() is not None:
            return
        await pilot.pause()
    raise AssertionError("opening the file did not reach the app")


def test_the_files_tab_snapshot(snap_compare: Callable[..., bool]) -> None:
    """The default tab: title, path, four tabs, listing and the hint bar."""
    build_fixture()
    assert snap_compare(make_app(), terminal_size=TERMINAL_SIZE, run_before=wait_for_listing)


def test_the_viewers_tab_snapshot(snap_compare: Callable[..., bool]) -> None:
    """``ctrl+2``: the same file the Files page opened, across the whole pane."""
    build_fixture()
    assert snap_compare(
        make_app(),
        press=["ctrl+2"],
        terminal_size=TERMINAL_SIZE,
        run_before=open_a_file_then_wait,
    )


def test_the_settings_tab_snapshot(snap_compare: Callable[..., bool]) -> None:
    """``ctrl+4``: the theme picker over the keybindings."""
    build_fixture()
    assert snap_compare(
        make_app(),
        press=["ctrl+4"],
        terminal_size=TERMINAL_SIZE,
        run_before=wait_for_listing,
    )


def test_the_light_theme_snapshot(snap_compare: Callable[..., bool]) -> None:
    """A theme switch repaints the same shell: the tokens are all that change."""
    build_fixture()
    app = make_app()

    async def switch_theme(pilot: Pilot) -> None:
        await wait_for_listing(pilot)
        pilot.app.set_theme("daylight-light")
        await pilot.pause()

    assert snap_compare(app, terminal_size=TERMINAL_SIZE, run_before=switch_theme)