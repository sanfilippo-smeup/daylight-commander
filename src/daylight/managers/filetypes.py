"""File-type counts for the standalone file-type dashboard.

Pure data reduction: no filesystem access and no Textual import. The walk
itself is :func:`daylight.managers.stats.build_stats`; this module only turns
its ``extension_counts`` mapping into ranked ``(label, count)`` rows a bar
chart can render.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["TOP_FILE_TYPES", "file_type_rows"]

#: How many file types the dashboard's bar chart shows.
TOP_FILE_TYPES = 20


def file_type_rows(
    counts: Mapping[str, int],
    *,
    extensionless: int = 0,
    limit: int = TOP_FILE_TYPES,
) -> list[tuple[str, int]]:
    """Rank ``counts`` by frequency, most files first.

    ``counts`` maps a lowercased extension without its dot to a file count.
    ``extensionless`` files get a ``(none)`` row so the chart accounts for the
    whole tree rather than only the files that happen to have a suffix. Ties
    sort by label, which keeps the output deterministic, and at most ``limit``
    rows are returned.
    """
    merged = {extension: count for extension, count in counts.items() if count}
    if extensionless:
        merged[""] = extensionless
    ranked = sorted(merged.items(), key=lambda item: (-item[1], item[0]))
    return [
        (f".{extension}" if extension else "(none)", count) for extension, count in ranked[:limit]
    ]
