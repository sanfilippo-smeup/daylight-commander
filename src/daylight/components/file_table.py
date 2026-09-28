"""Table-builder for the Files page.

Per ADR-006 the row building, sorting and filtering live here, separate from
the Textual widget that displays them. Everything in this module is a pure
function over :class:`daylight.models.DirEntry` values: no disk access, no UI.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from pathlib import Path

from daylight.models import DirEntry

__all__ = [
    "COLUMNS",
    "SCROLL_BUFFER",
    "SORT_KEYS",
    "build_row",
    "build_rows",
    "chain_entries",
    "filter_entries",
    "format_breadcrumb",
    "format_mtime",
    "format_permissions",
    "format_size",
    "icon_for",
    "next_sort_key",
    "sort_entries",
    "type_label",
    "window_bounds",
]

#: Sort keys offered by the Files page, in toggle order.
SORT_KEYS: tuple[str, ...] = ("name", "size", "time", "type")

#: Rows kept either side of the viewport, on top of the rows that fit on screen.
#:
#: The Files page passes ``0``: a DataTable that holds more rows than fit becomes
#: scrollable in its own right, and its scroll offset would then drift away from
#: the virtual offset the page scrolls by. The parameter stays so a caller that
#: can afford that (a table pinned to a fixed scroll position) may prefetch.
SCROLL_BUFFER = 0

#: ``(label, width)`` for every DataTable column, in display order.
COLUMNS: tuple[tuple[str, int], ...] = (
    ("Name", 0),
    ("Size", 9),
    ("Modified", 16),
    ("Perms", 10),
)

_TYPE_ICONS: dict[str, str] = {
    "parent": "\u2191",  # upwards arrow
    "dir": "\U0001f4c1",  # file folder
    "symlink": "\u2192",  # rightwards arrow
    "file": "\U0001f4c4",  # page facing up
}

_TYPE_LABELS: dict[str, str] = {
    "parent": "parent",
    "dir": "folder",
    "symlink": "symlink",
    "file": "file",
}

_EXTENSION_ICONS: dict[str, str] = {
    "py": "\U0001f40d",  # snake
    "md": "\U0001f4dd",  # memo
    "txt": "\U0001f4c4",  # page facing up
    "json": "{}",
    "toml": "\u2699",  # gear
    "yml": "\u2699",  # gear
    "yaml": "\u2699",  # gear
    "zip": "\U0001f5dc",  # floppy disk
    "gz": "\U0001f5dc",  # floppy disk
    "tar": "\U0001f5dc",  # floppy disk
    "png": "\U0001f5bc",  # framed picture
    "jpg": "\U0001f5bc",  # framed picture
    "mp3": "\U0001f3b5",  # musical note
    "sh": "$",
}

_SIZE_UNITS: tuple[str, ...] = ("B", "K", "M", "G", "T", "P")

#: Bytes per size unit: SI (1000), so 3000 bytes is displayed as ``3.0K``.
_SIZE_STEP = 1000


# -- formatting ------------------------------------------------------------


def format_size(entry: DirEntry) -> str:
    """Return a human readable size such as ``1.2K`` (``-`` for directories).

    Units are SI (1000 per step), so ``3000`` bytes reads as ``3.0K``.
    """
    if entry.is_dir:
        return "-"
    size = float(entry.size)
    for unit in _SIZE_UNITS:
        if size < _SIZE_STEP or unit == _SIZE_UNITS[-1]:
            if unit == "B":
                return f"{int(size)}{unit}"
            return f"{size:.1f}{unit}"
        size /= _SIZE_STEP
    return f"{size:.1f}{_SIZE_UNITS[-1]}"


def format_mtime(entry: DirEntry) -> str:
    """Return the modification time as ``YYYY-MM-DD HH:MM`` (``-`` for ``..``)."""
    if entry.is_parent:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.modified))


def format_permissions(entry: DirEntry) -> str:
    """Return the permission column text (``rwxr-xr-x``)."""
    if entry.is_parent:
        return ".."
    return entry.permissions


def type_label(entry: DirEntry) -> str:
    """Return the human readable type used for type sorting and the legend."""
    if entry.kind in ("parent", "dir", "symlink"):
        return _TYPE_LABELS[entry.kind]
    return entry.suffix or "file"


def icon_for(entry: DirEntry) -> str:
    """Return the type icon/emoji shown in front of the entry name."""
    if entry.kind == "parent":
        return _TYPE_ICONS["parent"]
    if entry.kind == "dir":
        return _TYPE_ICONS["dir"]
    if entry.suffix in _EXTENSION_ICONS:
        return _EXTENSION_ICONS[entry.suffix]
    return _TYPE_ICONS.get(entry.kind, _TYPE_ICONS["file"])


def format_breadcrumb(path: Path) -> str:
    """Return the breadcrumb text for ``path`` (``/`` for the filesystem root)."""
    if str(path) == path.anchor or not path.parts:
        return path.anchor or "/"
    return str(path)


# -- rows ------------------------------------------------------------------


def build_row(entry: DirEntry) -> tuple[str, ...]:
    """Return the display cells for one entry, in :data:`COLUMNS` order."""
    return (
        f"{icon_for(entry)} {entry.name}",
        format_size(entry),
        format_mtime(entry),
        format_permissions(entry),
    )


def build_rows(entries: Iterable[DirEntry]) -> Iterator[tuple[str, ...]]:
    """Lazily yield display rows; nothing is materialised up front."""
    for entry in entries:
        yield build_row(entry)


# -- virtualisation ---------------------------------------------------------


def window_bounds(
    offset: int,
    visible: int,
    total: int,
    buffer: int = SCROLL_BUFFER,
) -> tuple[int, int]:
    """Return the ``(start, stop)`` slice of the listing a table materialises.

    ``offset`` is the index of the first row on screen, ``visible`` how many rows
    fit in the viewport and ``total`` how many rows the listing holds. The result
    never covers more than ``visible + 2 * buffer`` rows, never runs past either
    end of the listing, and always contains the viewport: a 100k-row directory
    therefore only ever costs ``visible`` rows of work per redraw.
    """
    if total <= 0:
        return (0, 0)
    span = max(1, visible) + 2 * max(0, buffer)
    start = max(0, min(offset - max(0, buffer), total - span))
    return (start, min(total, start + span))


# -- filtering and sorting -------------------------------------------------


def filter_entries(entries: Iterable[DirEntry], text: str) -> Iterator[DirEntry]:
    """Yield the entries whose name contains ``text`` (case-insensitive).

    An empty or whitespace-only ``text`` passes every entry through, so the
    filter-as-you-type path stays allocation free while the user has not typed.
    """
    needle = text.strip().casefold()
    if not needle:
        yield from entries
        return
    for entry in entries:
        if needle in entry.name.casefold():
            yield entry


def _sort_tuple(entry: DirEntry, key: str) -> tuple:
    """Return the ordering tuple for ``entry`` under sort ``key``."""
    if key == "size":
        return (not entry.is_parent, not entry.is_dir, entry.size, entry.name.casefold())
    if key == "time":
        return (not entry.is_parent, -entry.modified, entry.name.casefold())
    if key == "type":
        return (not entry.is_parent, type_label(entry), entry.name.casefold())
    return (not entry.is_parent, not entry.is_dir, entry.name.casefold())


def sort_entries(
    entries: Iterable[DirEntry],
    key: str = "name",
    reverse: bool = False,
) -> list[DirEntry]:
    """Return ``entries`` sorted by ``key``; directories and ``..`` come first."""
    if key not in SORT_KEYS:
        raise ValueError(f"unknown sort key: {key!r}")
    ordered = sorted(entries, key=lambda entry: _sort_tuple(entry, key), reverse=reverse)
    if reverse:
        return [entry for entry in ordered if entry.is_parent] + [
            entry for entry in ordered if not entry.is_parent
        ]
    return ordered


def next_sort_key(current: str) -> str:
    """Return the sort key that follows ``current`` in toggle order."""
    if current not in SORT_KEYS:
        return SORT_KEYS[0]
    return SORT_KEYS[(SORT_KEYS.index(current) + 1) % len(SORT_KEYS)]


def chain_entries(
    entries: Iterable[DirEntry],
    text: str = "",
    sort_key: str = "name",
    reverse: bool = False,
) -> list[DirEntry]:
    """Filter then sort ``entries``; the result the table displays."""
    return sort_entries(filter_entries(entries, text), sort_key, reverse)
