"""Standalone dashboard: recursive file-type counts as a bar chart.

Run it against the default tree::

    uv run python -m daylight.filetype_dashboard

or point it anywhere::

    uv run python -m daylight.filetype_dashboard ~/projects

The scan is the same :func:`daylight.managers.stats.build_stats` walk the Stats
tab uses, so it keeps the 200k-entry / 15-second budget, never follows
symlinks and runs on a worker thread instead of blocking the event loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Static

from daylight import themes
from daylight.components.stats_charts import BarChart, SummaryCard, human_size
from daylight.managers.filetypes import file_type_rows
from daylight.managers.stats import build_stats
from daylight.models import AppConfig, DirectoryStats

__all__ = ["DEFAULT_ROOT", "FileTypeDashboard", "main"]

#: The tree the dashboard opens on when no argument is given.
DEFAULT_ROOT = Path("/Users/mauro/works")

#: Detail line appended to the title when the walk hit a safety cap.
PARTIAL_MARK = " (partial)"


class FileTypeDashboard(App[None]):
    """A one-screen dashboard whose bars count files by extension."""

    TITLE = "File types"
    CSS_PATH = "styles/filetypes.tcss"
    BINDINGS = [
        Binding("r", "refresh", "Refresh", show=True, id="refresh"),
        Binding("q", "quit", "Quit", show=True, id="quit"),
    ]

    def __init__(self, root: str | Path = DEFAULT_ROOT) -> None:
        self.root = Path(root).expanduser()
        # The shared charts ask the app for its theme, exactly as they do on
        # the Stats page; the default palette is enough for this standalone app.
        self.app_config = AppConfig()
        self._stats: DirectoryStats | None = None
        self._title = Static("", id="filetypes-title")
        self._cards = {label: SummaryCard(label) for label in ("Files", "File types", "Scan time")}
        self._chart = BarChart()
        self._chart.border_title = "Files by extension"
        super().__init__()

    # -- queries -----------------------------------------------------------

    @property
    def stats(self) -> DirectoryStats | None:
        """The last finished scan, or None while the first one runs."""
        return self._stats

    @property
    def chart(self) -> BarChart:
        """The bar chart, so tests can read the rows it was handed."""
        return self._chart

    def card(self, label: str) -> SummaryCard:
        """The summary card labelled ``label``."""
        return self._cards[label]

    def get_css_variables(self) -> dict[str, str]:
        """Publish the default theme's tokens to the dashboard stylesheet."""
        variables = super().get_css_variables()
        variables.update(themes.get_css_variables(themes.get_theme(self.app_config.theme)))
        return variables

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """The title line, three summary cards and the scrollable bar chart."""
        yield self._title
        with Horizontal(classes="stats-summary"):
            yield from self._cards.values()
        with VerticalScroll(id="filetypes-scroll"):
            yield self._chart

    # -- scan and render ---------------------------------------------------

    def on_mount(self) -> None:
        """Start the first scan once the app is composed."""
        self.action_refresh()

    def action_refresh(self) -> None:
        """Re-walk the tree and redraw the bars."""
        self._title.update(f" {self.root} \u2014 scanning\u2026")
        self._cards["Files"].set_value("\u2026")
        self._cards["File types"].set_value("\u2026")
        self._cards["Scan time"].set_value("\u2026")
        self._chart.set_bars([], format_value=str)
        self._scan(self.root)

    @work(thread=True, exclusive=True, group="filetypes")
    def _scan(self, root: Path) -> None:
        """Walk ``root`` off the event loop."""
        stats = build_stats(root)
        self.call_from_thread(self._show, stats)

    def _show(self, stats: DirectoryStats) -> None:
        """Draw one finished scan."""
        self._stats = stats
        mark = PARTIAL_MARK if stats.partial else ""
        self._title.update(
            f" {stats.root} \u2014 {stats.file_count} files, "
            f"{len(stats.extension_counts)} extensions{mark}"
        )
        self._cards["Files"].set_value(str(stats.file_count), human_size(stats.total_size))
        self._cards["File types"].set_value(str(len(stats.extension_counts)), "with an extension")
        self._cards["Scan time"].set_value(
            f"{stats.elapsed:.2f}s", f"{stats.scanned_entries} entries{mark}"
        )
        extensionless = stats.file_count - sum(stats.extension_counts.values())
        self._chart.set_bars(
            file_type_rows(stats.extension_counts, extensionless=extensionless),
            format_value=str,
        )


def main(argv: list[str] | None = None) -> None:
    """Run the dashboard on the first argument, or on :data:`DEFAULT_ROOT`."""
    arguments = sys.argv[1:] if argv is None else argv
    root = Path(arguments[0]) if arguments else DEFAULT_ROOT
    FileTypeDashboard(root).run()


if __name__ == "__main__":
    main()
