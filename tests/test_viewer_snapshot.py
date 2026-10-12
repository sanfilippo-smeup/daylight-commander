"""Snapshot tests for the inline viewer and the properties table (DC-004).

The fixture lives at a fixed path with pinned mtimes, so the breadcrumb, the
``Modified`` column and the ``Size`` header are deterministic no matter where the
repository is checked out. ``viewers`` is the metric these cover: the pane stays a
pane (one screen, both listings visible), each kind of file gets the body it
should, and navigation and search land where they should.

The properties table is *not* snapshotted: it shows the inode, which the
filesystem hands out afresh on every fixture rebuild, so such a snapshot could
never match on another machine or after a rebuild. Its fields, its copy key and
its status line are covered in ``test_viewer_page.py`` instead.
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
FIXTURE = Path(tempfile.gettempdir()) / "daylight-dc004-snapshot"

#: Pinned modification time (2026-03-04 05:06:07) for every fixture file.
MTIME = 1_772_601_567

TERMINAL_SIZE = (120, 40)

MARKDOWN = (
    "# Daylight\n"
    "\n"
    "A *commander* with two panes, a viewer and a [link](https://example.com).\n"
    "\n"
    "- reads through the viewer\n"
    "- edits through `$EDITOR`\n"
    "\n"
    "| key | value |\n"
    "| --- | ----- |\n"
    "| a   | 1     |\n"
    "\n"
    "```python\n"
    "x = 1\n"
    "```\n"
)


def build_fixture() -> Path:
    """Create the deterministic tree used by the viewer snapshots."""
    if FIXTURE.exists():
        shutil.rmtree(FIXTURE)
    FIXTURE.mkdir(parents=True)
    (FIXTURE / "doc.md").write_text(MARKDOWN, encoding="utf-8")
    (FIXTURE / "data.json").write_text('{"b":1,"a":[1,{"c":2}]}', encoding="utf-8")
    (FIXTURE / "code.py").write_text(
        "\n".join(f"line {number}" for number in range(1, 61)) + "\n", encoding="utf-8"
    )
    for target in FIXTURE.iterdir():
        os.utime(target, (MTIME, MTIME))
    os.utime(FIXTURE, (MTIME, MTIME))
    return FIXTURE


def make_app() -> DaylightApp:
    """An app pinned to the snapshot fixture with an isolated config file."""
    app = DaylightApp(AppConfig())
    app.start_path = FIXTURE
    app.config_manager = ConfigManager(Path(tempfile.gettempdir()) / "daylight-dc004-config.json")
    app.file_manager = FileManager()
    return app


async def wait_for_listing(pilot: Pilot) -> None:
    """Block until both panels listed the fixture, ``doc.md`` included.

    ``row_count`` alone is not a readiness signal here: the ``..`` row appears
    before the scan that fills the rest of the panel.
    """
    page = pilot.app.query_one(FilesPage)
    for _ in range(120):
        rows = {row.split(" ", 1)[1] for panel in page.panels for row in panel.visible_names()}
        if all(not panel.loading for panel in page.panels) and "doc.md" in rows:
            return
        await pilot.pause()
    raise AssertionError("the Files page did not finish listing")


async def finish(pilot: Pilot) -> None:
    """Let the workers finish and a whole refresh land before the screenshot.

    The viewer draws in a worker and repaints in a later message-pump cycle, so
    a screenshot taken the moment a window lands catches a half-drawn frame.
    """
    await pilot.app.workers.wait_for_complete()
    for _ in range(4):
        await pilot.pause()


def viewer_step(name: str) -> Callable[[Pilot], object]:
    """Return a ``run_before`` that puts the cursor on ``name`` and opens it."""

    async def run(pilot: Pilot) -> None:
        await wait_for_listing(pilot)
        page = pilot.app.query_one(FilesPage)
        rows = [row.split(" ", 1)[1] for row in page.active.visible_names()]
        page.active.move_cursor(rows.index(name))
        page.open_view()
        for _ in range(120):
            await pilot.pause()
            viewer = page.viewer
            if viewer is not None and viewer.is_mounted and not viewer.loading:
                await finish(pilot)
                return
        raise AssertionError(f"the viewer for {name} never finished loading")

    return run


def test_viewer_markdown_snapshot(snap_compare: Callable[..., bool]) -> None:
    """``f3`` on a Markdown file: rendered in the inactive pane, no screen pushed."""
    build_fixture()
    assert snap_compare(make_app(), terminal_size=TERMINAL_SIZE, run_before=viewer_step("doc.md"))


def test_viewer_json_snapshot(snap_compare: Callable[..., bool]) -> None:
    """``f3`` on JSON: pretty-printed, numbered, in the inactive pane."""
    build_fixture()
    assert snap_compare(
        make_app(), terminal_size=TERMINAL_SIZE, run_before=viewer_step("data.json")
    )


def test_viewer_text_window_snapshot(snap_compare: Callable[..., bool]) -> None:
    """``f3`` on plain text: one numbered window, scrolled two screens in."""

    async def run(pilot: Pilot) -> None:
        await viewer_step("code.py")(pilot)
        page = pilot.app.query_one(FilesPage)
        page.action_viewer_page_down()
        page.action_viewer_page_down()
        for _ in range(60):
            await pilot.pause()
            viewer = page.viewer
            if viewer is not None and not viewer.loading and viewer.line_start:
                await finish(pilot)
                return
        raise AssertionError("the window never moved")

    build_fixture()
    assert snap_compare(make_app(), terminal_size=TERMINAL_SIZE, run_before=run)


def test_viewer_search_snapshot(snap_compare: Callable[..., bool]) -> None:
    """``ctrl+f`` inside the viewer: the find box, the hit count and the match."""

    async def run(pilot: Pilot) -> None:
        await viewer_step("code.py")(pilot)
        page = pilot.app.query_one(FilesPage)
        page.action_search_viewer()
        page.viewer.search_input.value = "line 4"
        for _ in range(120):
            await pilot.pause()
            viewer = page.viewer
            if viewer is not None and not viewer.loading and viewer.hits:
                await finish(pilot)
                return
        raise AssertionError("the search never reported a match")

    build_fixture()
    assert snap_compare(make_app(), terminal_size=TERMINAL_SIZE, run_before=run)
