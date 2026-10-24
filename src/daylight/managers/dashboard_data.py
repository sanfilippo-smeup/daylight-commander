"""Dashboard data binder: spec sources -> deterministic render data (DC-029).

Pure derivation from a frozen :class:`daylight.models.DirectoryStats`: no
filesystem access of its own, no Textual, no component imports. Every
allowlisted source names a statistic :func:`daylight.managers.stats.build_stats`
already computed, so the binder only re-shapes, ranks and caps what a finished
scan produced.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from daylight.managers.format import human_size
from daylight.models import (
    DashboardBlock,
    DashboardBlockData,
    DashboardSpec,
    DashboardSummaryValue,
    DashboardTable,
    DirectoryStats,
)

__all__ = ["SERIES_SOURCES", "bind_block", "bind_spec"]

#: The sources bound as ranked ``(label, value)`` series rows, capped at ``top_n``.
SERIES_SOURCES: frozenset[str] = frozenset(
    (
        "extension_counts",
        "extension_sizes",
        "category_sizes",
        "monthly_counts",
        "permission_counts",
    )
)

#: The six summary values, in Stats page display order (DC-023).
_SUMMARY_LABELS: tuple[str, ...] = (
    "Total size",
    "Files",
    "Directories",
    "Dirs : files",
    "Hidden",
    "Symlinks",
)

#: Header row of both table sources, matching the Stats insights list.
_TABLE_HEADERS: tuple[str, ...] = ("#", "Name", "Size", "Modified")


def _mark(detail: str, partial: bool) -> str:
    """Append the ``(partial)`` marker to a card detail line."""
    if not partial:
        return detail
    return f"{detail} (partial)" if detail else "(partial)"


def _summary_values(stats: DirectoryStats) -> tuple[DashboardSummaryValue, ...]:
    """The six summary cards the Stats page shows, ``(partial)`` markers included."""
    partial = stats.partial
    return (
        DashboardSummaryValue(
            label="Total size",
            value=human_size(stats.total_size),
            detail=_mark(f"{stats.file_count} files", partial),
        ),
        DashboardSummaryValue(
            label="Files", value=str(stats.file_count), detail=_mark("", partial)
        ),
        DashboardSummaryValue(
            label="Directories", value=str(stats.directory_count), detail=_mark("", partial)
        ),
        DashboardSummaryValue(
            label="Dirs : files", value=stats.dirs_files_ratio, detail=_mark("", partial)
        ),
        DashboardSummaryValue(
            label="Hidden", value=str(stats.hidden_count), detail=_mark("dot entries", partial)
        ),
        DashboardSummaryValue(
            label="Symlinks", value=str(stats.symlink_count), detail=_mark("", partial)
        ),
    )


def _series(mapping: Mapping[str, int], top_n: int) -> tuple[tuple[str, int], ...]:
    """``(label, value)`` rows, most valuable first, labels ascending on ties.

    Sorting by ``(-value, label)`` makes the rows deterministic no matter how
    the source mapping happens to be ordered, and the label tie-break keeps a
    cut at ``top_n`` stable between identical scans.
    """
    ranked = sorted(mapping.items(), key=lambda item: (-item[1], item[0]))
    return tuple(ranked[:top_n])


def _day(modified: float) -> str:
    """A file mtime as the date the tables show."""
    return datetime.fromtimestamp(modified).strftime("%Y-%m-%d")


def _largest_table(stats: DirectoryStats) -> DashboardTable:
    """The largest files of ``stats``, largest first, as string table rows."""
    rows = [
        (str(rank), entry.name, human_size(entry.size), _day(entry.modified))
        for rank, entry in enumerate(stats.largest, start=1)
    ]
    return DashboardTable(headers=_TABLE_HEADERS, rows=tuple(rows))


def _insights_table(stats: DirectoryStats) -> DashboardTable:
    """The insights list: the largest files plus the oldest and newest rows."""
    rows = list(_largest_table(stats).rows)
    if stats.oldest is not None:
        rows.append(
            ("old", stats.oldest.name, human_size(stats.oldest.size), _day(stats.oldest.modified))
        )
    if stats.newest is not None:
        rows.append(
            ("new", stats.newest.name, human_size(stats.newest.size), _day(stats.newest.modified))
        )
    return DashboardTable(headers=_TABLE_HEADERS, rows=tuple(rows))


def bind_block(block: DashboardBlock, stats: DirectoryStats) -> DashboardBlockData:
    """Bind one block's allowlisted source against ``stats`` into render data."""
    if block.source == "summary":
        return DashboardBlockData(block=block, summary=_summary_values(stats))
    if block.source in SERIES_SOURCES:
        source_data: Mapping[str, int] = getattr(stats, block.source)
        return DashboardBlockData(block=block, rows=_series(source_data, block.top_n))
    if block.source == "size_buckets":
        return DashboardBlockData(block=block, rows=tuple(stats.size_buckets.items()))
    if block.source == "daily_counts":
        return DashboardBlockData(block=block, days=dict(stats.daily_counts))
    if block.source == "largest_files":
        return DashboardBlockData(block=block, table=_largest_table(stats))
    if block.source == "insights":
        return DashboardBlockData(block=block, table=_insights_table(stats))
    raise ValueError(f"unknown dashboard data source: {block.source!r}")


def bind_spec(spec: DashboardSpec, stats: DirectoryStats) -> tuple[DashboardBlockData, ...]:
    """Bind every block of ``spec`` against ``stats``, in specification order."""
    return tuple(bind_block(block, stats) for block in spec.blocks)
