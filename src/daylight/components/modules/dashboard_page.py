"""The AI Dashboard tab: renders one DashboardSpec natively (DC-030, ADR-016).

The app hands the page a validated :class:`daylight.models.DashboardSpec` and a
directory; the page walks the directory with :func:`build_stats` on a worker
thread and renders one widget per block with the Stats page's chart components
and theme tokens (FR-805, FR-807). Block titles are untrusted agent output, so
every one of them is rendered as plain text: the title line uses a
``markup=False`` static and block titles are passed to ``border_title`` as Rich
``Text`` objects, which never parses markup (DC-029 F6). Data-bound values go
through the DC-029 binder, so the summary cards, the table rows and the bucket
order are exactly the Stats page's.

``compose`` runs once: block widgets are mounted and removed dynamically per
bound spec, and a finished scan is installed only while its request generation
still matches, so a stale scan -- from a superseded dashboard or a queued
refresh -- can never overwrite the dashboard on screen.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from daylight.components.stats_charts import (
    BarChart,
    CalendarHeatmap,
    Histogram,
    ShareBar,
    SummaryCard,
    active_theme,
)
from daylight.managers.dashboard_data import bind_spec
from daylight.managers.format import human_size
from daylight.managers.stats import build_stats
from daylight.models import (
    DashboardBlock,
    DashboardBlockData,
    DashboardSpec,
    DashboardTable,
    DirectoryStats,
    Theme,
)

__all__ = ["COMPUTING_MESSAGE", "EMPTY_MESSAGE", "DashboardPage", "TableBlock"]

#: The placeholder shown before any dashboard is bound (DC-030).
EMPTY_MESSAGE = "No dashboard yet — build or load a dashboard from the Builder tab."

#: The placeholder shown while a dashboard's statistics are being computed (AC4).
COMPUTING_MESSAGE = "Computing…"

#: The series sources whose values are byte sizes; every other series is a count.
_SIZE_SOURCES: frozenset[str] = frozenset(("extension_sizes",))


def _default_title(block: DashboardBlock) -> str:
    """A block without its own title: its source, humanized (AC3)."""
    return block.source.replace("_", " ").capitalize()


def _value_format(source: str) -> Callable[[int], str]:
    """How a bar chart reads values: bytes for size sources, plain counts otherwise."""
    return human_size if source in _SIZE_SOURCES else str


class TableBlock(Static):
    """One table block: a Rich grid of DC-029 headers over deterministic rows.

    Cells are rendered as ``Text`` objects in theme colors; no cell is ever
    parsed as markup because the rows are agent-shaped output (AC3).
    """

    def __init__(self) -> None:
        self.table: DashboardTable = DashboardTable(headers=(), rows=())
        super().__init__(classes="dashboard-block", markup=False)

    def set_table(self, table: DashboardTable) -> None:
        """Show ``table``: the header row over the bound data rows, in order."""
        self.table = table
        self._redraw()

    def _redraw(self) -> None:
        theme: Theme = active_theme(self)
        grid = Table.grid(padding=(0, 2))
        for column, _header in enumerate(self.table.headers):
            grid.add_column(justify="right" if column == 0 else None)
        if self.table.headers:
            grid.add_row(*(Text(header, style=theme.muted) for header in self.table.headers))
        for row in self.table.rows:
            grid.add_row(*(Text(cell, style=theme.foreground) for cell in row))
        self.update(grid)


class DashboardPage(VerticalScroll):
    """The dedicated AI-dashboard tab: spec-driven blocks over one directory.

    The app mounts the page once and reuses it for every dashboard; the page is
    bound to the directory it was opened with, not the active pane's (AC6). A
    dashboard bound before the page is mounted starts on :meth:`on_mount`.
    """

    DEFAULT_CLASSES = "dashboard-page"
    BINDINGS = [
        Binding(
            "ctrl+r",
            "refresh_dashboard",
            "Refresh",
            show=True,
            id="refresh_dashboard",
        ),
    ]

    def __init__(self, stats_provider: Callable[[Path], DirectoryStats] | None = None) -> None:
        #: Where a block's numbers come from: :func:`build_stats` by default, or
        #: an injected provider so tests and snapshots render without the disk.
        self._stats_provider = stats_provider or build_stats
        self._spec: DashboardSpec | None = None
        self._directory: Path | None = None
        self._stats: DirectoryStats | None = None
        #: Bumped on every compute request; a finished scan whose generation no
        #: longer matches is stale and must not overwrite a newer dashboard (AC4).
        self._generation = 0
        self._title = Static("", id="dashboard-title", markup=False)
        self._empty = Static(EMPTY_MESSAGE, classes="dashboard-empty", markup=False)
        #: The one blocks area: created empty here (compose runs once, and a
        #: dashboard can be bound before the page is even composed), so blocks
        #: are mounted and removed dynamically instead of rebuilding the page.
        self._blocks = Vertical(id="dashboard-blocks")
        super().__init__(id="dashboard-page")

    # -- queries -----------------------------------------------------------

    @property
    def stats(self) -> DirectoryStats | None:
        """The statistics on screen, or None before the first scan finishes."""
        return self._stats

    @property
    def directory(self) -> Path | None:
        """The directory this dashboard is bound to."""
        return self._directory

    @property
    def title_text(self) -> str:
        """The title line as plain text."""
        return str(self._title.content)

    def summary_card(self, label: str) -> SummaryCard | None:
        """The rendered summary card labelled ``label``, or None before a render."""
        for card in self.query(SummaryCard):
            if card.card_label == label:
                return card
        return None

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """The title line, the placeholder, and the blocks area (runs once)."""
        yield self._title
        yield self._empty
        yield self._blocks

    # -- binding and computing (AC4) ---------------------------------------

    def on_mount(self) -> None:
        """Start a dashboard that was bound before the page was mounted."""
        if self._spec is not None:
            self._start_scan()

    def set_dashboard(self, spec: DashboardSpec, directory: Path | str) -> None:
        """Bind ``spec`` to ``directory`` and recompute the statistics (AC4).

        Safe before the page is mounted: the request is remembered and
        :meth:`on_mount` starts the scan once the page is in the DOM. The gate
        is ``is_mounted``, not ``is_attached``, because the first
        ``open_dashboard`` mounts the page and binds it in the same turn: there
        the page is attached while ``on_mount`` has yet to run, and gating on
        ``is_attached`` would start a scan that races a second one from
        :meth:`on_mount` (DC-030 F1). One of the two paths fires per bind, in
        either ordering.
        """
        self._spec = spec
        self._directory = Path(directory)
        if self.is_mounted:
            self._start_scan()

    def action_refresh_dashboard(self) -> None:
        """``ctrl+r``: recompute the bound directory, bypassing any cache (AC4)."""
        if self._spec is None or self._directory is None:
            return
        self._start_scan()

    def _start_scan(self) -> None:
        """Bump the request generation, show the computing state, and scan."""
        self._generation += 1
        self._draw_waiting()
        self._scan(self._generation, self._directory)

    @work(thread=True, exclusive=True, group="dashboard")
    def _scan(self, generation: int, directory: Path) -> None:
        """Walk ``directory`` off the event loop (FR-808)."""
        stats = self._stats_provider(directory)
        self.app.call_from_thread(self._apply, generation, directory, stats)

    def _apply(self, generation: int, directory: Path, stats: DirectoryStats) -> None:
        """Install a finished scan unless a newer request superseded it (AC4)."""
        if generation != self._generation or directory != self._directory:
            return  # a stale scan cannot overwrite the dashboard on screen
        self._stats = stats
        self._draw(stats)

    # -- rendering ---------------------------------------------------------

    def _block_widgets(self, data: DashboardBlockData) -> list[Widget]:
        """The widgets one bound block renders as (AC2)."""
        block = data.block
        title = Text(block.title or _default_title(block))
        if block.kind == "summary_cards":
            cards = [SummaryCard(value.label) for value in data.summary]
            for card, value in zip(cards, data.summary):
                card.set_value(value.value, value.detail)
            wrapper = Horizontal(*cards, classes="dashboard-block dashboard-summary")
            wrapper.border_title = title
            return [wrapper]
        if block.kind == "bar_chart":
            chart = BarChart()
            chart.set_bars(
                data.rows,
                color_token=block.color_token,
                format_value=_value_format(block.source),
            )
            chart.border_title = title
            chart.add_class("dashboard-block")
            return [chart]
        if block.kind == "share_bar":
            share = ShareBar()
            # DC-029 ranks the parts; zero-valued ones are not drawn, exactly
            # like the Stats page's share bar (DC-029 F3).
            share.set_share([(label, value) for label, value in data.rows if value > 0])
            share.border_title = title
            share.add_class("dashboard-block")
            return [share]
        if block.kind == "histogram":
            histogram = Histogram()
            # ``size_buckets`` rows keep their bucket order (DC-029).
            histogram.set_columns(data.rows, color_token=block.color_token)
            histogram.border_title = title
            histogram.add_class("dashboard-block")
            return [histogram]
        if block.kind == "heatmap":
            heatmap = CalendarHeatmap()
            heatmap.set_days(data.days or {})
            heatmap.border_title = title
            heatmap.add_class("dashboard-block")
            return [heatmap]
        if block.kind == "table":
            table = TableBlock()
            table.set_table(data.table or DashboardTable(headers=(), rows=()))
            table.border_title = title
            return [table]
        return []  # unreachable: kind/source pairs are allowlisted (DC-029)

    def _clear_blocks(self) -> None:
        """Unmount the rendered blocks, keeping the placeholder ready."""
        blocks = list(self._blocks.children)
        if blocks:
            self._blocks.remove_children(blocks)

    def _draw_waiting(self) -> None:
        """Empty the dashboard and say the tree is being computed (AC4)."""
        assert self._spec is not None and self._directory is not None
        self._title.update(f"{self._spec.title} — {self._directory} — computing…")
        self._empty.update(COMPUTING_MESSAGE)
        self._empty.display = True
        self._clear_blocks()

    def _draw(self, stats: DirectoryStats) -> None:
        """Render one widget per block of the bound spec (AC2)."""
        spec = self._spec
        if spec is None:
            return
        partial = " (partial)" if stats.partial else ""
        self._title.update(
            f"{spec.title} — {stats.root} — {stats.scanned_entries} entries{partial}"
        )
        widgets: list[Widget] = []
        for data in bind_spec(spec, stats):
            widgets.extend(self._block_widgets(data))
        self._clear_blocks()
        if widgets:
            self._blocks.mount(*widgets)
            self._empty.display = False
