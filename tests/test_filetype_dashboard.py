"""File-type dashboard tests: ranking rules and a real recursive scan.

The ranking is pure and deterministic; the pilot scenario builds its own
temp tree so the bar values do not depend on the developer's home directory.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from textual.pilot import Pilot

from daylight.filetype_dashboard import FileTypeDashboard
from daylight.managers.filetypes import TOP_FILE_TYPES, file_type_rows

#: Generous ceiling: the worker thread needs a few turns to finish a scan.
SETTLE = 120

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def run_async(fn: F) -> F:
    """Run an async test body on a fresh event loop (no pytest-asyncio needed)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


def build_tree(root: Path) -> Path:
    """Three Python files, one Markdown file and one extension-less file."""
    root.mkdir(parents=True)
    for name in ("a.py", "b.py", "c.py", "notes.md", "README"):
        (root / name).write_text("x", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "nested.py").write_text("x", encoding="utf-8")
    return root


async def wait_for_scan(pilot: Pilot) -> FileTypeDashboard:
    """Block until the dashboard finished its first scan."""
    app = pilot.app
    assert isinstance(app, FileTypeDashboard)
    for _ in range(SETTLE):
        if app.stats is not None:
            return app
        await pilot.pause()
    raise AssertionError("the dashboard did not finish its scan")


# -- ranking -----------------------------------------------------------------


def test_rows_rank_by_count_then_label() -> None:
    """Most frequent first, ties alphabetical, extensionless labelled."""
    rows = file_type_rows({"py": 3, "md": 5, "json": 1}, extensionless=2)
    assert rows == [(".md", 5), (".py", 3), ("(none)", 2), (".json", 1)]


def test_rows_respect_the_limit_and_drop_zero_counts() -> None:
    """Empty extensions do not clutter the chart and ``limit`` caps the rows."""
    rows = file_type_rows({"py": 2, "md": 0, "json": 1, "txt": 1}, limit=2)
    assert rows == [(".py", 2), (".json", 1)]
    assert TOP_FILE_TYPES >= 8


# -- dashboard ---------------------------------------------------------------


@run_async
async def test_dashboard_charts_recursive_counts(tmp_path: Path) -> None:
    """The bars count the whole tree, nested files and extensionless included."""
    tree = build_tree(tmp_path / "types")
    app = FileTypeDashboard(tree)
    async with app.run_test(size=(100, 30)) as pilot:
        app = await wait_for_scan(pilot)
        assert app.chart.rows == [(".py", 4), ("(none)", 1), (".md", 1)]
        assert app.card("Files").card_value == "6"
        assert app.card("File types").card_value == "2"
        assert str(tree) in str(app._title.content)


@run_async
async def test_refresh_rescans(tmp_path: Path) -> None:
    """``r`` re-walks the tree, so a new file appears in the bars."""
    tree = build_tree(tmp_path / "types")
    app = FileTypeDashboard(tree)
    async with app.run_test(size=(100, 30)) as pilot:
        app = await wait_for_scan(pilot)
        (tree / "extra.json").write_text("x", encoding="utf-8")
        await pilot.press("r")
        for _ in range(SETTLE):
            if (".json", 1) in app.chart.rows:
                break
            await pilot.pause()
        assert (".json", 1) in app.chart.rows
