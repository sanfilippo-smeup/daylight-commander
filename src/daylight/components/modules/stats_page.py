"""The Stats tab: a read-only dashboard of the active pane's directory (DC-023).

Every number comes from :func:`daylight.managers.stats.build_stats`, computed
on a worker thread -- the same ``@work(thread=True, exclusive=True, group=...)``
pattern the Files page scans with -- so a huge tree never blocks the event loop.
Results are cached per directory: showing the tab for a directory that was
already scanned re-renders the cache, and ``ctrl+r`` throws the directory on
screen out of the cache and recomputes it.
"""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Static

from daylight.components.stats_charts import (
    BarChart,
    CalendarHeatmap,
    Histogram,
    ShareBar,
    SummaryCard,
    active_theme,
    human_size,
    insights_table,
)
from daylight.managers.stats import build_stats
from daylight.models import DirectoryStats

__all__ = ["SUMMARY_CARDS", "TOP_CHART_ROWS", "TOP_MONTHS", "StatsPage"]

#: The six summary cards, in display order (AC2).
SUMMARY_CARDS: tuple[str, ...] = (
    "Total size",
    "Files",
    "Directories",
    "Dirs : files",
    "Hidden",
    "Symlinks",
)

#: How many extensions / permission masks the bar charts show.
TOP_CHART_ROWS = 8

#: How many months the monthly bar chart shows.
TOP_MONTHS = 12


def _mark(detail: str, partial: bool) -> str:
    """Append the ``(partial)`` marker to a card detail line (AC5)."""
    if not partial:
        return detail
    return f"{detail} (partial)" if detail else "(partial)"


def _top_extensions(stats: DirectoryStats) -> list[tuple[str, int]]:
    """The largest extensions by total size, most bytes first."""
    ranked = sorted(stats.extension_sizes.items(), key=lambda item: -item[1])
    return [(f".{name}", size) for name, size in ranked[:TOP_CHART_ROWS]]


def _monthly_bars(stats: DirectoryStats) -> list[tuple[str, int]]:
    """The most recent months present in the data, oldest first, capped."""
    return sorted(stats.monthly_counts.items())[-TOP_MONTHS:]


def _permission_bars(stats: DirectoryStats) -> list[tuple[str, int]]:
    """The most common permission masks, most files first."""
    ranked = sorted(stats.permission_counts.items(), key=lambda item: -item[1])
    return ranked[:TOP_CHART_ROWS]


class StatsPage(VerticalScroll):
    """The fifth tab: summary cards and charts for one directory tree."""

    DEFAULT_CLASSES = "stats-page"
    BINDINGS = [
        Binding("ctrl+r", "refresh_stats", "Refresh", show=True, id="refresh_stats"),
    ]

    def __init__(self) -> None:
        #: Finished scans by directory; a tab revisit renders from here.
        self._cache: dict[Path, DirectoryStats] = {}
        self._current: Path | None = None
        self._stats: DirectoryStats | None = None
        self._title = Static("", id="stats-title")
        self._cards = {label: SummaryCard(label) for label in SUMMARY_CARDS}
        self._share = ShareBar()
        self._extensions = BarChart()
        self._heatmap = CalendarHeatmap()
        self._monthly = BarChart()
        self._histogram = Histogram()
        self._permissions = BarChart()
        self._insights = Static(classes="insights")
        self._share.border_title = "Disk usage by category"
        self._extensions.border_title = "Top file extensions"
        self._heatmap.border_title = "Modification calendar"
        self._monthly.border_title = "Files per month"
        self._histogram.border_title = "Size distribution"
        self._permissions.border_title = "Permission masks"
        self._insights.border_title = "Largest / oldest / newest"
        super().__init__(id="stats-page")

    # -- queries -----------------------------------------------------------

    @property
    def stats(self) -> DirectoryStats | None:
        """The stats currently on screen, or None while the first scan runs."""
        return self._stats

    @property
    def current_directory(self) -> Path | None:
        """The directory the dashboard is pointed at."""
        return self._current

    @property
    def title_text(self) -> str:
        """The dashboard title line as plain text."""
        return str(self._title.content)

    def card(self, label: str) -> SummaryCard:
        """The summary card labelled ``label``."""
        return self._cards[label]

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """The title line and the scrollable grid of cards and charts."""
        yield self._title
        with Horizontal(classes="stats-summary"):
            yield from self._cards.values()
        with Horizontal(classes="stats-row"):
            yield self._share
            yield self._extensions
        with Horizontal(classes="stats-row"):
            yield self._heatmap
            yield self._histogram
        with Horizontal(classes="stats-row"):
            yield self._permissions
            yield self._insights

    # -- refresh (AC6) -----------------------------------------------------

    def on_mount(self) -> None:
        """Start the first scan once the page is in the DOM.

        The shell prepares a page between scheduling its mount and its
        composition, so the first :meth:`refresh_stats` can arrive too early to
        act; mounting is what makes the request real.
        """
        self.refresh_stats()

    def refresh_stats(self, directory: Path | None = None) -> None:
        """Point the dashboard at ``directory`` (default: the active pane's).

        A cached result is rendered immediately; otherwise the charts are
        cleared, the title says the tree is being computed, and a worker thread
        starts the walk. Called before the page is attached to the DOM, an
        explicit ``directory`` is remembered and :meth:`on_mount` starts the
        scan instead.
        """
        if not self.is_attached:
            if directory is not None:
                self._current = Path(directory)
            return
        target = Path(directory) if directory is not None else self.app.current_path()
        self._current = target
        cached = self._cache.get(target)
        if cached is not None:
            self._draw(cached)
            return
        self._draw_waiting(target)
        self._scan(target)

    def action_refresh_stats(self) -> None:
        """``ctrl+r``: recompute the directory on screen, bypassing the cache."""
        if self._current is None:
            self.refresh_stats()
            return
        self._cache.pop(self._current, None)
        self.refresh_stats(self._current)

    @work(thread=True, exclusive=True, group="stats")
    def _scan(self, directory: Path) -> None:
        """Walk ``directory`` off the event loop (AC5)."""
        stats = build_stats(directory)
        self.app.call_from_thread(self._apply, directory, stats)

    def _apply(self, directory: Path, stats: DirectoryStats) -> None:
        """Install a finished scan unless a newer directory was requested."""
        if directory != self._current:
            return
        self._cache[directory] = stats
        self._draw(stats)

    # -- rendering ---------------------------------------------------------

    def _draw_waiting(self, directory: Path) -> None:
        """Empty the dashboard and say the tree is being computed."""
        self._title.update(f" {directory} \u2014 computing\u2026")
        for label in SUMMARY_CARDS:
            self._cards[label].set_value("\u2026")
        self._share.set_share([])
        self._extensions.set_bars([])
        self._heatmap.set_days({})
        self._monthly.set_bars([])
        self._histogram.set_columns([])
        self._permissions.set_bars([])
        self._insights.update("")

    def _draw(self, stats: DirectoryStats) -> None:
        """Draw every card and chart from one finished scan."""
        self._stats = stats
        partial = stats.partial
        self._title.update(
            f" {stats.root} \u2014 {stats.scanned_entries} entries{_mark('', partial)}"
        )
        self._cards["Total size"].set_value(
            human_size(stats.total_size), _mark(f"{stats.file_count} files", partial)
        )
        self._cards["Files"].set_value(str(stats.file_count), _mark("", partial))
        self._cards["Directories"].set_value(str(stats.directory_count), _mark("", partial))
        self._cards["Dirs : files"].set_value(stats.dirs_files_ratio, _mark("", partial))
        self._cards["Hidden"].set_value(str(stats.hidden_count), _mark("dot entries", partial))
        self._cards["Symlinks"].set_value(str(stats.symlink_count), _mark("", partial))
        self._share.set_share(
            [(name, size) for name, size in stats.category_sizes.items() if size > 0]
        )
        self._extensions.set_bars(_top_extensions(stats))
        self._heatmap.set_days(stats.daily_counts)
        self._monthly.set_bars(_monthly_bars(stats), format_value=str)
        self._histogram.set_columns(list(stats.size_buckets.items()))
        self._permissions.set_bars(
            _permission_bars(stats), color_token="secondary", format_value=str
        )
        self._insights.update(insights_table(stats, active_theme(self)))
