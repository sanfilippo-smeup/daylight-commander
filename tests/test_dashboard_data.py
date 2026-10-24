"""Unit tests for the dashboard data binder (DC-029).

Hermetic: the binder is fed synthetic frozen ``DirectoryStats`` objects built
in memory; the tests never touch a filesystem, which mirrors the binder's own
guarantee (AC4: it only re-shapes what ``build_stats`` already computed).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from daylight.managers.dashboard_data import SERIES_SOURCES, bind_block, bind_spec
from daylight.managers.dashboard_spec import parse_spec
from daylight.managers.stats import SIZE_BUCKETS
from daylight.models import (
    DashboardBlock,
    DashboardBlockData,
    DirectoryStats,
    FileStat,
)

#: Pinned modification time (2026-03-04 05:06:07 local) so table dates compare.
MTIME = 1_772_601_567

#: The pinned mtime as the binder formats it in table rows.
DAY = datetime.fromtimestamp(MTIME).strftime("%Y-%m-%d")

#: Deterministic source mappings the synthetic stats carry.
CATEGORY_SIZES: dict[str, int] = {
    "Code": 1024,
    "Documents": 112,
    "Images": 400,
    "Media": 0,
    "Archives": 0,
    "Data": 0,
    "Other": 0,
}
EXTENSION_SIZES: dict[str, int] = {"md": 400, "py": 1024, "txt": 112}
EXTENSION_COUNTS: dict[str, int] = {"md": 3, "py": 2, "txt": 1}
MONTHLY_COUNTS: dict[str, int] = {"2026-02": 2, "2025-12": 5, "2026-01": 2}
PERMISSION_COUNTS: dict[str, int] = {"-rw-r--r--": 4, "-rwxr-xr-x": 2}
DAILY_COUNTS: dict[date, int] = {date(2026, 3, 1): 2, date(2026, 3, 4): 4}

#: The valid kind for each source, for building blocks outside the parser.
KIND_OF: dict[str, str] = {
    "summary": "summary_cards",
    "extension_counts": "bar_chart",
    "extension_sizes": "bar_chart",
    "category_sizes": "share_bar",
    "size_buckets": "histogram",
    "monthly_counts": "bar_chart",
    "permission_counts": "bar_chart",
    "daily_counts": "heatmap",
    "largest_files": "table",
    "insights": "table",
}


def make_stats(**overrides: Any) -> DirectoryStats:
    """A synthetic ``DirectoryStats`` with deterministic, non-trivial values.

    The ``monthly_counts`` mapping is deliberately inserted out of order so
    ranking tests prove the binder sorts instead of trusting dict order.
    """
    fields: dict[str, Any] = dict(
        root=Path("/data/tree"),
        total_size=1536,
        file_count=6,
        directory_count=2,
        hidden_count=1,
        symlink_count=1,
        scanned_entries=10,
        partial=False,
        elapsed=0.25,
        category_sizes=dict(CATEGORY_SIZES),
        extension_sizes=dict(EXTENSION_SIZES),
        extension_counts=dict(EXTENSION_COUNTS),
        size_buckets=dict(zip((label for label, _ in SIZE_BUCKETS), (1, 2, 0, 3, 0, 0, 0, 0, 0))),
        permission_counts=dict(PERMISSION_COUNTS),
        daily_counts=dict(DAILY_COUNTS),
        monthly_counts=dict(MONTHLY_COUNTS),
        largest=(
            FileStat(path=Path("/data/tree/big.bin"), name="big.bin", size=2048, modified=MTIME),
            FileStat(path=Path("/data/tree/small.txt"), name="small.txt", size=9, modified=MTIME),
        ),
        oldest=FileStat(
            path=Path("/data/tree/small.txt"), name="small.txt", size=9, modified=MTIME - 100
        ),
        newest=FileStat(path=Path("/data/tree/big.bin"), name="big.bin", size=2048, modified=MTIME),
    )
    fields.update(overrides)
    return DirectoryStats(**fields)


def bound(source: str, stats: DirectoryStats | None = None, **overrides: Any) -> DashboardBlockData:
    """Bind ``source`` against synthetic stats, with an optional top_n."""
    block = DashboardBlock(kind=KIND_OF[source], source=source, **overrides)
    return bind_block(block, make_stats() if stats is None else stats)


# -- summary source -------------------------------------------------------------


def test_summary_binds_the_six_stats_values_in_display_order() -> None:
    """The six values match the Stats page labels, in order, empty otherwise."""
    data = bound("summary")
    assert [value.label for value in data.summary] == [
        "Total size",
        "Files",
        "Directories",
        "Dirs : files",
        "Hidden",
        "Symlinks",
    ]
    assert [value.value for value in data.summary] == ["1.5 KiB", "6", "2", "2:6", "1", "1"]
    assert [value.detail for value in data.summary] == [
        "6 files",
        "",
        "",
        "",
        "dot entries",
        "",
    ]
    assert data.rows == () and data.days is None and data.table is None


def test_partial_markers_propagate_to_every_summary_value() -> None:
    """``stats.partial`` marks all six cards, appended or standalone (AC5)."""
    data = bound("summary", make_stats(partial=True))
    assert [value.detail for value in data.summary] == [
        "6 files (partial)",
        "(partial)",
        "(partial)",
        "(partial)",
        "dot entries (partial)",
        "(partial)",
    ]


def test_summary_without_files_and_partial_shows_a_standalone_marker() -> None:
    """An empty partial tree gets the bare ``(partial)`` on every card."""
    stats = make_stats(partial=True, file_count=0, total_size=0)
    data = bound("summary", stats)
    assert [value.detail for value in data.summary] == [
        "0 files (partial)",
        "(partial)",
        "(partial)",
        "(partial)",
        "dot entries (partial)",
        "(partial)",
    ]


# -- ranked series sources --------------------------------------------------------


@pytest.mark.parametrize(
    "source,mapping",
    [
        ("extension_counts", EXTENSION_COUNTS),
        ("extension_sizes", EXTENSION_SIZES),
        ("category_sizes", CATEGORY_SIZES),
        ("monthly_counts", MONTHLY_COUNTS),
        ("permission_counts", PERMISSION_COUNTS),
    ],
)
def test_series_sources_bind_ranked_rows(source: str, mapping: dict[str, int]) -> None:
    """Every series source yields ``(label, value)`` rows, most valuable first."""
    data = bound(source)
    expected = tuple(sorted(mapping.items(), key=lambda item: (-item[1], item[0])))
    assert data.rows == expected
    assert data.summary == () and data.days is None and data.table is None


def test_series_caps_at_top_n() -> None:
    """A validated block's ``top_n`` caps the rows."""
    assert bound("extension_counts", top_n=2).rows == (("md", 3), ("py", 2))
    assert len(bound("extension_counts").rows) == 3


def test_series_tie_break_is_the_label_ascending() -> None:
    """Equal values tie-break by label, not by dictionary insertion order."""
    assert bound("monthly_counts").rows == (
        ("2025-12", 5),
        ("2026-01", 2),
        ("2026-02", 2),
    )


def test_series_cut_keeps_the_label_smallest_on_a_boundary_tie() -> None:
    """Capping at ``top_n`` on a tie keeps the alphabetically first label."""
    stats = make_stats(extension_counts={"txt": 2, "py": 2, "md": 1})
    assert bound("extension_counts", stats, top_n=2).rows == (("py", 2), ("txt", 2))


def test_series_is_deterministic_under_mapping_reordering() -> None:
    """Two dict orderings of identical content produce identical rows."""
    reordered = make_stats(permission_counts={"-rwxr-xr-x": 2, "-rw-r--r--": 4})
    normal = make_stats(permission_counts={"-rw-r--r--": 4, "-rwxr-xr-x": 2})
    assert bound("permission_counts", reordered).rows == bound("permission_counts", normal).rows
    assert bound("permission_counts", reordered).rows == (
        ("-rw-r--r--", 4),
        ("-rwxr-xr-x", 2),
    )


def test_all_series_sources_are_allowlisted_members_of_the_binder() -> None:
    """The binder's SERIES_SOURCES only names DirectoryStats mapping fields."""
    stats = make_stats()
    for source in SERIES_SOURCES:
        block = DashboardBlock(kind=KIND_OF[source], source=source)
        data = bind_block(block, stats)
        assert data.rows and len(data.rows) <= getattr(stats, source).__len__()


# -- size buckets -----------------------------------------------------------------


def test_size_buckets_keep_the_stats_bucket_order() -> None:
    """Histogram rows follow the Stats bucket order, not the ranking rule."""
    data = bound("size_buckets")
    labels = [label for label, _ in SIZE_BUCKETS]
    assert [label for label, _ in data.rows] == labels
    assert data.rows[2] == (labels[2], 0)
    assert data.rows[3] == (labels[3], 3)
    # Not ranked: the count 3 sits in the fourth bucket, not the first.
    assert data.rows != tuple(sorted(data.rows, key=lambda row: -row[1]))


# -- daily counts ------------------------------------------------------------------


def test_daily_counts_bind_as_a_date_to_count_mapping() -> None:
    """The heatmap gets a ``date -> count`` mapping, decoupled from the stats."""
    stats = make_stats()
    data = bound("daily_counts", stats)
    assert data.days == {date(2026, 3, 1): 2, date(2026, 3, 4): 4}
    assert data.days is not stats.daily_counts
    assert data.rows == () and data.table is None and data.summary == ()


# -- table sources ------------------------------------------------------------------


def test_largest_files_bind_as_a_deterministic_table() -> None:
    """Header plus one row per file of ``stats.largest``, largest first."""
    table = bound("largest_files").table
    assert table is not None
    assert table.headers == ("#", "Name", "Size", "Modified")
    assert table.rows == (
        ("1", "big.bin", "2.0 KiB", DAY),
        ("2", "small.txt", "9 B", DAY),
    )


def test_insights_bind_largest_first_then_the_old_and_new_rows() -> None:
    """The insights table extends the largest list with the dated extremes."""
    table = bound("insights").table
    assert table is not None
    old_day = datetime.fromtimestamp(MTIME - 100).strftime("%Y-%m-%d")
    assert table.headers == ("#", "Name", "Size", "Modified")
    assert table.rows[:2] == (
        ("1", "big.bin", "2.0 KiB", DAY),
        ("2", "small.txt", "9 B", DAY),
    )
    assert table.rows[2] == ("old", "small.txt", "9 B", old_day)
    assert table.rows[3] == ("new", "big.bin", "2.0 KiB", DAY)


def test_tables_collapse_to_a_header_only_table_without_files() -> None:
    """A tree with no files yields empty rows, not an error."""
    stats = make_stats(largest=(), oldest=None, newest=None)
    largest = bound("largest_files", stats).table
    insights = bound("insights", stats).table
    assert largest is not None and insights is not None
    assert largest.rows == () and insights.rows == ()
    assert largest.headers == insights.headers


# -- whole-spec binding ---------------------------------------------------------------


def test_bind_spec_binds_every_block_in_specification_order() -> None:
    """A parsed spec binds block for block; the block rides along untouched."""
    spec = parse_spec(
        {
            "schema_version": 1,
            "title": "Tree overview",
            "blocks": [
                {"kind": "summary_cards", "source": "summary"},
                {"kind": "bar_chart", "source": "extension_counts", "top_n": 2},
                {"kind": "share_bar", "source": "category_sizes"},
                {"kind": "histogram", "source": "size_buckets"},
                {"kind": "heatmap", "source": "daily_counts"},
                {"kind": "table", "source": "largest_files"},
            ],
        }
    )
    data = bind_spec(spec, make_stats())
    assert tuple(item.block for item in data) == spec.blocks
    assert data[0].summary and data[1].rows and data[2].rows
    assert data[3].rows and data[4].days and data[5].table


def test_bind_block_rejects_a_source_outside_the_binder() -> None:
    """A block built outside the parser cannot smuggle an unknown source in."""
    block = DashboardBlock(kind="table", source="nonsense")
    with pytest.raises(ValueError, match="unknown dashboard data source"):
        bind_block(block, make_stats())
