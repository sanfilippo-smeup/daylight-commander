"""Snapshot tests for the dual-pane Files page (DC-002 ``dualpane`` metric).

The fixture lives at a fixed path and its mtimes are pinned, so the rendered
breadcrumb and the Modified column are deterministic no matter where the
repository is checked out (``tempfile.gettempdir()`` is ``/tmp`` on macOS and
Linux).
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
FIXTURE = Path(tempfile.gettempdir()) / "daylight-dc002-snapshot"

#: Pinned modification time (2026-03-04 05:06:07) for every fixture file.
MTIME = 1_772_601_567

TERMINAL_SIZE = (120, 40)


def build_fixture() -> Path:
    """Create the deterministic listing tree used by the snapshots."""
    if FIXTURE.exists():
        shutil.rmtree(FIXTURE)
    FIXTURE.mkdir(parents=True)
    (FIXTURE / "docs").mkdir()
    contents = {
        "alpha.txt": "a" * 10,
        "beta.py": "b" * 3000,
        "gamma.md": "g" * 20,
        "docs/deep.md": "dd",
    }
    for name, text in contents.items():
        target = FIXTURE / name
        target.write_text(text, encoding="utf-8")
    # Pin the directories as well: writing the files inside them updates their own
    # mtime, which the Modified column would otherwise render as "now".
    for target in (FIXTURE / "docs", FIXTURE):
        os.utime(target, (MTIME, MTIME))
    for name in contents:
        os.utime(FIXTURE / name, (MTIME, MTIME))
    return FIXTURE


def make_app() -> DaylightApp:
    """An app pinned to the snapshot fixture with an isolated config file."""
    app = DaylightApp(AppConfig())
    app.start_path = FIXTURE
    app.config_manager = ConfigManager(Path(tempfile.gettempdir()) / "daylight-dc002-config.json")
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


def test_dual_pane_default_snapshot(snap_compare: Callable[..., bool]) -> None:
    """Two panels side by side, the left one active and focused."""
    build_fixture()
    assert snap_compare(make_app(), terminal_size=TERMINAL_SIZE, run_before=wait_for_listing)


def test_dual_pane_after_switching_snapshot(snap_compare: Callable[..., bool]) -> None:
    """``ctrl+t`` moves the active panel: exactly one stays active."""
    build_fixture()
    assert snap_compare(
        make_app(),
        press=["ctrl+t"],
        terminal_size=TERMINAL_SIZE,
        run_before=wait_for_listing,
    )


def test_dual_pane_filtered_snapshot(snap_compare: Callable[..., bool]) -> None:
    """``/`` reveals the filter input and narrows the active panel's rows."""
    build_fixture()

    async def filter_then_wait(pilot: Pilot) -> None:
        await wait_for_listing(pilot)
        await pilot.press("slash")
        # "alph" matches exactly one fixture row (alpha.txt); a bare "a" would
        # also match beta.py and gamma.md.
        await pilot.press(*"alph")
        for _ in range(60):
            page = pilot.app.query_one(FilesPage)
            if page.active.row_count == 1:
                return
            await pilot.pause()
        raise AssertionError("the filter did not narrow the rows")

    assert snap_compare(make_app(), terminal_size=TERMINAL_SIZE, run_before=filter_then_wait)


def test_sort_indicator_snapshot(snap_compare: Callable[..., bool]) -> None:
    """The active sort column carries an arrow in the header (DC-009)."""

    async def sort_by_size(pilot: Pilot) -> None:
        page = pilot.app.query_one(FilesPage)
        left = page.panel("left")
        for _ in range(60):
            if not left.loading and left.row_count:
                break
            await pilot.pause()
        await pilot.press("s")  # name -> size
        for _ in range(60):
            if left.sort_key == "size" and not left.loading:
                return
            await pilot.pause()
        raise AssertionError("the sort did not switch to size")

    build_fixture()
    assert snap_compare(make_app(), terminal_size=TERMINAL_SIZE, run_before=sort_by_size)
