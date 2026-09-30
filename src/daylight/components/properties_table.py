"""Table-builder for the properties/detail view.

Per ADR-006 the field list and its formatting live here, separate from the widget
that displays them: a pure function over a
:class:`daylight.models.FileProperties`, no disk access, no UI. Every value is a
plain string so the view can put any of them on the clipboard (FR-303).
"""

from __future__ import annotations

import time

from daylight.models import FileProperties

__all__ = ["COLUMNS", "FIELDS", "build_row", "build_rows", "copy_value", "value_at"]

#: ``(label, width)`` for every DataTable column, in display order.
COLUMNS: tuple[tuple[str, int], ...] = (("Field", 14), ("Value", 0))

#: The property fields, in display order. Also the copy order.
FIELDS: tuple[str, ...] = (
    "name",
    "path",
    "type",
    "content",
    "mime",
    "size",
    "inode",
    "links",
    "permissions",
    "octal",
    "owner",
    "group",
    "modified",
    "changed",
    "accessed",
    "created",
    "checksum",
)

_SIZE_UNITS: tuple[str, ...] = ("B", "K", "M", "G", "T", "P")

_SIZE_STEP = 1000

#: ``%Y-%m-%d %H:%M:%S``; the one format every timestamp in the view uses.
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_size(size: int) -> str:
    """Return ``size`` bytes as ``1234 (1.2K)``: exact count plus a rounded unit."""
    value = float(size)
    for unit in _SIZE_UNITS:
        if value < _SIZE_STEP or unit == _SIZE_UNITS[-1]:
            return f"{size} ({int(value)}{unit})" if unit == "B" else f"{size} ({value:.1f}{unit})"
        value /= _SIZE_STEP
    return f"{size} ({value:.1f}{_SIZE_UNITS[-1]})"


def format_time(stamp: float | None) -> str:
    """Return a local timestamp, or ``-`` when the platform does not provide one."""
    if not stamp:
        return "-"
    return time.strftime(_TIME_FORMAT, time.localtime(stamp))


def _checksum_text(properties: FileProperties) -> str:
    """Return the checksum, or a dash for a directory, which has none."""
    if not properties.checksum:
        return "-"
    return f"{properties.checksum_algorithm}:{properties.checksum}"


def build_row(properties: FileProperties, field: str) -> tuple[str, str]:
    """Return the ``(field label, value)`` cells for one property field."""
    values = {
        "name": ("Name", properties.name),
        "path": ("Path", str(properties.path)),
        "type": ("Type", properties.view_kind),
        "content": ("Content", "text" if properties.is_text else "binary"),
        "mime": ("MIME", properties.mime),
        "size": ("Size", format_size(properties.size)),
        "inode": ("Inode", str(properties.inode)),
        "links": ("Hard links", str(properties.links)),
        "permissions": ("Permissions", properties.permissions),
        "octal": ("Octal", properties.octal_permissions),
        "owner": ("Owner", properties.owner),
        "group": ("Group", properties.group),
        "modified": ("Modified", format_time(properties.modified)),
        "changed": ("Changed", format_time(properties.changed)),
        "accessed": ("Accessed", format_time(properties.accessed)),
        "created": ("Created", format_time(properties.created)),
        "checksum": ("Checksum", _checksum_text(properties)),
    }
    if field not in values:
        raise ValueError(f"unknown property field: {field!r}")
    return values[field]


def build_rows(properties: FileProperties) -> list[tuple[str, str]]:
    """Return every property row, in :data:`FIELDS` order."""
    return [build_row(properties, field) for field in FIELDS]


def value_at(properties: FileProperties, index: int) -> str:
    """Return the value of row ``index``, or an empty string when out of range."""
    if not 0 <= index < len(FIELDS):
        return ""
    return build_row(properties, FIELDS[index])[1]


def copy_value(properties: FileProperties, index: int) -> str:
    """Return the value of row ``index``: what the copy key puts on the clipboard."""
    return value_at(properties, index)
