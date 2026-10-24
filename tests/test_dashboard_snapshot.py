"""One committed pytest-textual-snapshot of the Dashboard page (DC-032).

The Dashboard tab is captured inside the real app shell (its ``index.tcss``
styles and theme tokens are part of what the picture proves) with its
``stats_provider`` seam fed a fixed synthetic :class:`DirectoryStats` and a
fixed two-block specification (summary cards + bar chart). The scan therefore
never touches the disk, and every number on the screen is synthetic, so the
snapshot is deterministic and independent of ``tmp_path`` — the dashboard's
rendered text carries only the fixed relative root ``dashboard-fixture``. The
Files page starts listing a fixed directory under the OS temp root (this
repository's snapshot-fixture convention); nothing it lists reaches the
captured Dashboard tab.

DC-031's bounded wait helpers are reused (imported from ``test_builder_page``)
instead of sleeps: the run waits for real states — the shell listing, the
dashboard tab, the finished scan and the six mounted cards.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest
from textual.pilot import Pilot

from daylight.app import DaylightApp
from daylight.components.modules.dashboard_page import DashboardPage
from daylight.components.stats_charts import BarChart, SummaryCard
from daylight.models import (
    DashboardBlock,
    DashboardSpec,
    DirectoryStats,
    FileStat,
)

from test_builder_page import SIZE, make_app, wait_for, wait_for_listing

#: Fixed, checkout-independent directory the app's Files page starts in. The
#: snapshot captures only the Dashboard tab; the listing of this directory
#: never reaches the screen.
START_DIR = Path(tempfile.gettempdir()) / "daylight-dc032-snapshot"

#: The fixed relative root the synthetic statistics carry. It is rendered in
#: the dashboard's title line, so it must be a literal, not a temp path.
FIXTURE_ROOT = Path("dashboard-fixture")

#: Pinned modification time (2026-03-04 05:06:07 local) for the synthetic files.
MTIME = 1_772_601_567.0

#: The synthetic day key the heatmap sources would read (not rendered here).
DAY = date(2026, 3, 4)

#: The fixed two-block specification: summary cards + bar chart (AC5).
SNAPSHOT_SPEC = DashboardSpec(
    schema_version=1,
    title="Snapshot dashboard",
    blocks=(
        DashboardBlock(kind="summary_cards", source="summary"),
        DashboardBlock(kind="bar_chart", source="extension_counts", top_n=3),
    ),
)


def fixed_stats() -> DirectoryStats:
    """The fixed synthetic ``DirectoryStats`` the snapshot renders (no disk)."""
    return DirectoryStats(
        root=FIXTURE_ROOT,
        total_size=4096,
        file_count=5,
        directory_count=1,
        hidden_count=1,
        symlink_count=0,
        scanned_entries=6,
        partial=False,
        elapsed=0.5,
        category_sizes={"Code": 1024, "Documents": 512, "Images": 2048, "Empty": 0},
        extension_sizes={"py": 2048, "md": 1024},
        extension_counts={"md": 4, "py": 3, "csv": 1},
        size_buckets={"<1K": 4, "1-10K": 1},
        permission_counts={"rw-r--r--": 5},
        daily_counts={DAY: 3},
        monthly_counts={"2026-03": 5},
        largest=(
            FileStat(path=FIXTURE_ROOT / "big.py", name="big.py", size=2048, modified=MTIME),
        ),
        oldest=FileStat(
            path=FIXTURE_ROOT / "old.log", name="old.log", size=10, modified=MTIME
        ),
        newest=FileStat(
            path=FIXTURE_ROOT / "new.txt", name="new.txt", size=32, modified=MTIME
        ),
    )


class _FixedStatsDashboardPage(DashboardPage):
    """The Dashboard page backed by the fixed synthetic stats provider."""

    def __init__(self) -> None:
        super().__init__(stats_provider=lambda _directory: fixed_stats())


def make_snapshot_app() -> DaylightApp:
    """An app pinned to the fixed start directory with an isolated config."""
    START_DIR.mkdir(parents=True, exist_ok=True)
    (START_DIR / "note.txt").write_text(
        "deterministic listing row\n", encoding="utf-8"
    )
    return make_app(root=START_DIR)


async def open_snapshot_dashboard(pilot: Pilot) -> None:
    """Open the captured dashboard and wait until its widgets are on screen."""
    await wait_for_listing(pilot)  # the shell composed; the listing settled
    app = pilot.app
    assert isinstance(app, DaylightApp)
    app.open_dashboard(SNAPSHOT_SPEC, FIXTURE_ROOT)
    await wait_for(pilot, lambda: app.tabs.get_tab("dashboard") is not None)
    page = app._pages["dashboard"]
    assert isinstance(page, DashboardPage)
    await wait_for(pilot, lambda: page.stats is not None)
    await wait_for(pilot, lambda: len(list(page.query(SummaryCard))) == 6)
    chart = page.query_one(BarChart)
    assert chart.rows == [("md", 4), ("py", 3), ("csv", 1)]
    await pilot.pause()


def test_the_dashboard_tab_renders_the_spec_natively_snapshot(
    monkeypatch: pytest.MonkeyPatch, snap_compare: Callable[..., bool]
) -> None:
    """The committed Dashboard tab: six cards + bar chart, natively rendered."""
    monkeypatch.setattr(
        DaylightApp, "PAGES", {**DaylightApp.PAGES, "dashboard": _FixedStatsDashboardPage}
    )
    assert snap_compare(
        make_snapshot_app(), terminal_size=SIZE, run_before=open_snapshot_dashboard
    )