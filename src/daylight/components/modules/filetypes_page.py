"""The File types tab: files grouped by extension for the active pane (DC-034).

Every number comes from :func:`daylight.managers.stats.build_stats`, computed on
a worker thread with the same safety cap the Stats tab uses, so a huge tree
never blocks the event loop. Results are cached per directory: showing the tab
for a directory that was already scanned re-renders the cache, and ``ctrl+r``
throws the directory on screen out of the cache and recomputes it.

The ranked rows themselves are pure data reduction
(:func:`daylight.managers.filetypes.file_type_rows`), so the counting rules are
unit-testable without a UI.
"""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Static

from daylight.components.stats_charts import BarChart, SummaryCard, human_size
from daylight.managers.filetypes import file_type_rows
from daylight.managers.stats import build_stats
from daylight.models import DirectoryStats

__all__ = ["CARDS", "FileTypesPage"]

#: The three summary cards, in display order.
CARDS: tuple[str, ...] = ("Files", "File types", "Scan time")


def _mark(detail: str, partial: bool) -> str:
    """Append the ``(partial)`` marker to a card detail line."""
    if not partial:
        return detail
    return f"{detail} (partial)" if detail else "(partial)"


class FileTypesPage(VerticalScroll):
    """The seventh tab: file counts by extension for one directory tree."""

    BINDINGS = [
        Binding("ctrl+r", "refresh_filetypes", "Refresh", show=True, id="refresh_filetypes"),
    ]

    def __init__(self) -> None:
        #: Finished scans by directory; a tab revisit renders from here.
        self._cache: dict[Path, DirectoryStats] = {}
        self._current: Path | None = None
        self._stats: DirectoryStats | None = None
        self._title = Static("", id="filetypes-title")
        self._cards = {label: SummaryCard(label) for label in CARDS}
        self._chart = BarChart()
        self._chart.border_title = "Files by extension"
        super().__init__(id="filetypes-page")

    # -- queries -----------------------------------------------------------

    @property
    def stats(self) -> DirectoryStats | None:
        """The scan currently on screen, or None while the first one runs."""
        return self._stats

    @property
    def current_directory(self) -> Path | None:
        """The directory the page is pointed at."""
        return self._current

    @property
    def title_text(self) -> str:
        """The title line as plain text."""
        return str(self._title.content)

    @property
    def chart(self) -> BarChart:
        """The bar chart, so tests can read the rows it was handed."""
        return self._chart

    def card(self, label: str) -> SummaryCard:
        """The summary card labelled ``label``."""
        return self._cards[label]

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """The title line, three summary cards and the scrollable bar chart."""
        yield self._title
        with Horizontal(classes="stats-summary"):
            yield from self._cards.values()
        with VerticalScroll(id="filetypes-scroll"):
            yield self._chart

    # -- refresh -----------------------------------------------------------

    def on_mount(self) -> None:
        """Start the first scan once the page is in the DOM.

        The shell prepares a page between scheduling its mount and its
        composition, so the first :meth:`refresh_filetypes` can arrive too early
        to act; mounting is what makes the request real.
        """
        self.refresh_filetypes()

    def refresh_filetypes(self, directory: Path | None = None) -> None:
        """Point the page at ``directory`` (default: the active pane's).

        A cached result is rendered immediately; otherwise the cards are cleared,
        the title says the tree is being scanned, and a worker thread starts the
        walk. Called before the page is attached to the DOM, an explicit
        ``directory`` is remembered and :meth:`on_mount` starts the scan.
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

    def action_refresh_filetypes(self) -> None:
        """``ctrl+r``: recompute the directory on screen, bypassing the cache."""
        if self._current is None:
            self.refresh_filetypes()
            return
        self._cache.pop(self._current, None)
        self.refresh_filetypes(self._current)

    @work(thread=True, exclusive=True, group="filetypes")
    def _scan(self, directory: Path) -> None:
        """Walk ``directory`` off the event loop."""
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
        """Empty the page and say the tree is being scanned."""
        self._title.update(f" {directory} \u2014 scanning\u2026")
        for label in CARDS:
            self._cards[label].set_value("\u2026")
        self._chart.set_bars([], format_value=str)

    def _draw(self, stats: DirectoryStats) -> None:
        """Draw the cards and the chart from one finished scan."""
        self._stats = stats
        partial = stats.partial
        extensionless = stats.file_count - sum(stats.extension_counts.values())
        self._title.update(
            f" {stats.root} \u2014 {stats.file_count} files, "
            f"{len(stats.extension_counts)} extensions{_mark('', partial)}"
        )
        self._cards["Files"].set_value(
            str(stats.file_count), _mark(human_size(stats.total_size), partial)
        )
        self._cards["File types"].set_value(
            str(len(stats.extension_counts)), _mark("with an extension", partial)
        )
        self._cards["Scan time"].set_value(
            f"{stats.elapsed:.2f}s", _mark(f"{stats.scanned_entries} entries", partial)
        )
        self._chart.set_bars(
            file_type_rows(stats.extension_counts, extensionless=extensionless),
            format_value=str,
        )
