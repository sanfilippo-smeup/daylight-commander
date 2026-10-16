"""Dependency-free Rich charts for the Stats page (DC-023, ADR-013).

Every chart is a Textual :class:`~textual.widgets.Static` that renders a Rich
renderable built from plain data the page hands it. There is no plotting
library and no image: bars are block characters, the heatmap is
background-colored cells, and every color comes from the active
:class:`daylight.models.Theme` through :mod:`daylight.themes` -- never from a
literal and never from a ``.tcss`` file.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Callable, ClassVar

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual.types import NoActiveAppError
from textual.widget import Widget
from textual.widgets import Static

from daylight import themes
from daylight.models import DirectoryStats, Theme

__all__ = [
    "BarChart",
    "CalendarHeatmap",
    "Histogram",
    "ShareBar",
    "SummaryCard",
    "active_theme",
    "human_size",
    "insights_rows",
    "insights_table",
]


def active_theme(widget: Widget) -> Theme:
    """The theme the running app has active; the default when detached.

    A chart may be asked to render between its page being mounted and its own
    composition, when it has no app to ask yet; the default theme keeps that
    first render alive until the next refresh draws the real one.
    """
    try:
        return themes.get_theme(widget.app.app_config.theme)
    except NoActiveAppError:
        return themes.DEFAULT_THEME


def human_size(size: int | float) -> str:
    """``1536`` -> ``1.5 KiB``: a byte count as a person reads it."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PiB"


def _blend(first: str, second: str, factor: float) -> str:
    """Mix two ``#rrggbb`` colors; ``0.0`` returns ``first``, ``1.0`` the second."""

    def channels(hex_color: str) -> tuple[int, int, int]:
        return int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)

    start, end = channels(first), channels(second)
    mixed = tuple(round(a + (b - a) * factor) for a, b in zip(start, end))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _day(modified: float) -> str:
    """A file mtime as the date the insights list shows."""
    return datetime.fromtimestamp(modified).strftime("%Y-%m-%d")


class SummaryCard(Static):
    """One summary tile: a small label, a large value, and an optional detail."""

    def __init__(self, label: str) -> None:
        self.card_label = label
        self.card_value = ""
        self.card_detail = ""
        super().__init__(classes="summary-card")

    def set_value(self, value: str, detail: str = "") -> None:
        """Replace the value and detail lines, keeping the card's label."""
        self.card_value = value
        self.card_detail = detail
        self._redraw()

    def _redraw(self) -> None:
        theme = active_theme(self)
        grid = Table.grid()
        grid.add_column()
        grid.add_row(Text(self.card_label, style=theme.muted))
        grid.add_row(Text(self.card_value, style=f"bold {theme.accent}"))
        if self.card_detail:
            grid.add_row(Text(self.card_detail, style=theme.muted))
        self.update(grid)


class BarChart(Static):
    """Horizontal bars, one row per ``(label, value)`` pair."""

    #: Width of the bar area, in cells; the largest value fills it.
    BAR_CELLS: ClassVar[int] = 24
    #: Width of the label column, in cells.
    LABEL_CELLS: ClassVar[int] = 14

    def __init__(self) -> None:
        self.rows: list[tuple[str, int]] = []
        self._color_token = "accent"
        self._format_value: Callable[[int], str] = human_size
        super().__init__(classes="chart")

    def set_bars(
        self,
        rows: Sequence[tuple[str, int]],
        *,
        color_token: str = "accent",
        format_value: Callable[[int], str] = human_size,
    ) -> None:
        """Show one bar per row; the largest value fills the bar area."""
        self.rows = list(rows)
        self._color_token = color_token
        self._format_value = format_value
        self._redraw()

    def _redraw(self) -> None:
        theme = active_theme(self)
        color = getattr(theme, self._color_token)
        grid = Table.grid(padding=(0, 1))
        grid.add_column(justify="right", width=self.LABEL_CELLS)
        grid.add_column(width=self.BAR_CELLS)
        grid.add_column(justify="right")
        peak = max((value for _, value in self.rows), default=0)
        for label, value in self.rows:
            filled = round(value / peak * self.BAR_CELLS) if peak else 0
            bar = Text("\u2588" * filled + "\u2591" * (self.BAR_CELLS - filled), style=color)
            grid.add_row(
                Text(label, style=theme.foreground),
                bar,
                Text(self._format_value(value), style=theme.muted),
            )
        self.update(grid)


class ShareBar(Static):
    """A 100% stacked share bar with a legend, one segment per part."""

    #: Width of the stacked bar, in cells.
    BAR_CELLS: ClassVar[int] = 40
    #: Theme tokens the segments cycle through, in part order.
    TOKENS: ClassVar[tuple[str, ...]] = (
        "accent",
        "secondary",
        "success",
        "warning",
        "error",
        "foreground",
        "muted",
    )

    def __init__(self) -> None:
        self.parts: list[tuple[str, int]] = []
        super().__init__(classes="chart")

    def set_share(self, parts: Sequence[tuple[str, int]]) -> None:
        """Show ``parts`` -- ``(label, value)`` pairs -- as one stacked bar."""
        self.parts = list(parts)
        self._redraw()

    def _redraw(self) -> None:
        theme = active_theme(self)
        total = sum(value for _, value in self.parts)
        if not self.parts or not total:
            self.update(Text(""))
            return
        bar = Text()
        legend = Table.grid(padding=(0, 2))
        legend.add_column(justify="center", width=2)
        legend.add_column()
        legend.add_column(justify="right")
        for index, (label, value) in enumerate(self.parts):
            color = getattr(theme, self.TOKENS[index % len(self.TOKENS)])
            cells = round(value / total * self.BAR_CELLS)
            bar.append("\u2588" * cells, style=color)
            share = f"{value / total * 100:.0f}%"
            legend.add_row(
                Text("\u2588\u2588", style=color),
                Text(label, style=theme.foreground),
                Text(f"{human_size(value)}  {share}", style=theme.muted),
            )
        self.update(Group(bar, legend))


class CalendarHeatmap(Static):
    """A ~12-week calendar heatmap: one column per week, one row per weekday.

    The grid is preceded by a reference-period caption and a month header row,
    each row is prefixed with its weekday initial, and the legend maps the
    shades to a numeric ``0 \u2026 peak`` scale so the pace of activity is
    readable.
    """

    #: How many weeks the calendar spans.
    WEEKS: ClassVar[int] = 12
    #: How many intensity levels a day can have, empty included.
    LEVELS: ClassVar[int] = 5
    #: Row labels, Monday first.
    WEEKDAYS: ClassVar[tuple[str, ...]] = ("M", "T", "W", "T", "F", "S", "S")

    def __init__(self) -> None:
        self.counts: Mapping[date, int] = {}
        self.anchor: date | None = None
        super().__init__(classes="chart")

    def set_days(self, counts: Mapping[date, int]) -> None:
        """Show ``counts`` as a 12-week calendar ending at the newest activity.

        The window ends at the most recent day in ``counts`` -- a tree last
        touched months ago shows its own active weeks instead of an empty grid
        -- and at today when there is no data at all.
        """
        self.counts = dict(counts)
        self.anchor = max(self.counts) if self.counts else date.today()
        self._redraw()

    def window(self) -> tuple[date, date]:
        """The inclusive ``(start, end)`` dates the grid covers.

        ``start`` is the Monday of the oldest week, ``end`` the newest active
        day (or today), so the caption always names the exact period shown.
        """
        end = self.anchor or date.today()
        week_start = end - timedelta(days=end.weekday())
        first = week_start - timedelta(weeks=self.WEEKS - 1)
        return first, end

    def level_colors(self, theme: Theme) -> list[str]:
        """The cell colors, from empty to busiest: ``panel`` blended to ``success``."""
        return [
            _blend(theme.panel, theme.success, step / (self.LEVELS - 1))
            for step in range(self.LEVELS)
        ]

    def _month_labels(self, first: date) -> list[str]:
        """Per-week-column abbreviated month, blank where the month repeats."""
        labels: list[str] = []
        previous: str | None = None
        for week in range(self.WEEKS):
            label = (first + timedelta(weeks=week)).strftime("%b")
            labels.append(label if label != previous else "")
            previous = label
        return labels

    def _redraw(self) -> None:
        theme = active_theme(self)
        colors = self.level_colors(theme)
        peak = max(self.counts.values(), default=0)
        first, end = self.window()
        caption = Text(
            f"{self.WEEKS} weeks \u00b7 {first.isoformat()} \u2192 {end.isoformat()}",
            style=theme.muted,
        )
        month_row = Text("  ")
        for label in self._month_labels(first):
            month_row.append(f"{label:>3}", style=theme.muted)
        canvas = Text()
        for weekday in range(7):
            canvas.append(f"{self.WEEKDAYS[weekday]} ", style=theme.muted)
            for week in range(self.WEEKS):
                day = first + timedelta(weeks=week, days=weekday)
                if day > end:
                    canvas.append("   ")
                    continue
                count = self.counts.get(day, 0)
                level = 0
                if count and peak:
                    level = 1 + min(self.LEVELS - 2, round(count / peak * (self.LEVELS - 2)))
                canvas.append("   ", style=f"on {colors[level]}")
            canvas.append("\n")
        legend = Text()
        for color in colors:
            legend.append("  ", style=f"on {color}")
        legend.append("  0", style=theme.muted)
        if peak:
            legend.append(f"  \u2026  {peak}/day", style=theme.muted)
        self.update(Group(caption, month_row, canvas, legend))


class Histogram(Static):
    """A vertical histogram: one column per ``(label, count)`` pair."""

    #: Height of the bar area, in rows; the largest count fills it.
    HEIGHT: ClassVar[int] = 7

    def __init__(self) -> None:
        self.columns: list[tuple[str, int]] = []
        self._color_token = "accent"
        super().__init__(classes="chart")

    def set_columns(
        self, columns: Sequence[tuple[str, int]], *, color_token: str = "accent"
    ) -> None:
        """Show one column per pair; the largest count fills the height."""
        self.columns = list(columns)
        self._color_token = color_token
        self._redraw()

    def _redraw(self) -> None:
        theme = active_theme(self)
        color = getattr(theme, self._color_token)
        if not self.columns:
            self.update(Text(""))
            return
        peak = max(count for _, count in self.columns)
        grid = Table.grid(padding=(0, 1))
        for _ in self.columns:
            grid.add_column(justify="center")
        for row in range(self.HEIGHT, 0, -1):
            grid.add_row(
                *(
                    Text(
                        "\u2587" if peak and round(count / peak * self.HEIGHT) >= row else " ",
                        style=color,
                    )
                    for _, count in self.columns
                )
            )
        grid.add_row(*(Text(label, style=theme.muted) for label, _ in self.columns))
        self.update(grid)


def insights_rows(stats: DirectoryStats) -> tuple[tuple[str, str, str, str], ...]:
    """``(marker, name, size, modified)`` rows for the insights list.

    The largest files ranked first, then the oldest and the newest. Pure data,
    so tests can assert it without rendering anything.
    """
    rows: list[tuple[str, str, str, str]] = [
        (str(rank), entry.name, human_size(entry.size), _day(entry.modified))
        for rank, entry in enumerate(stats.largest, start=1)
    ]
    if stats.oldest is not None:
        rows.append(
            ("old", stats.oldest.name, human_size(stats.oldest.size), _day(stats.oldest.modified))
        )
    if stats.newest is not None:
        rows.append(
            ("new", stats.newest.name, human_size(stats.newest.size), _day(stats.newest.modified))
        )
    return tuple(rows)


def insights_table(stats: DirectoryStats, theme: Theme) -> RenderableType:
    """The top-N / oldest / newest list as a Rich table in ``theme`` colors."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", width=4)
    grid.add_column()
    grid.add_column(justify="right")
    grid.add_column(justify="right")
    for marker, name, size, day in insights_rows(stats):
        grid.add_row(
            Text(marker, style=theme.muted),
            Text(name, style=theme.foreground),
            Text(size, style=theme.accent),
            Text(day, style=theme.muted),
        )
    return grid
