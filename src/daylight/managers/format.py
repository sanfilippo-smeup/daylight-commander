"""Pure formatting helpers shared by managers and components (DC-029).

Moved out of :mod:`daylight.components.stats_charts` so a manager (the
dashboard data binder) can format byte counts without importing component
code; the component module re-exports :func:`human_size` for compatibility.
"""

from __future__ import annotations

__all__ = ["human_size"]


def human_size(size: int | float) -> str:
    """``1536`` -> ``1.5 KiB``: a byte count as a person reads it."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PiB"
