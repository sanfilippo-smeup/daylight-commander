"""Recursive directory statistics for the Stats tab (DC-023, ADR-013).

Pure filesystem work: this module never imports Textual or any widget code, so
it can run on a worker thread while the UI keeps drawing. :func:`build_stats`
walks a tree once with :func:`os.walk`, classifies every entry it can read, and
reduces the result into a frozen :class:`daylight.models.DirectoryStats`.

Two safety caps bound the walk: at most :data:`MAX_STAT_ENTRIES` entries are
classified and at most :data:`STATS_TIME_BUDGET_SECONDS` seconds are spent.
Hitting either one stops the walk and marks the result ``partial=True``, so the
dashboard can label the affected cards "(partial)" instead of blocking or
exhausting memory on a huge tree.
"""

from __future__ import annotations

import os
import stat
from datetime import date, datetime
from heapq import heapreplace, heappush
from itertools import count
from pathlib import Path
from time import monotonic

from daylight.models import DirectoryStats, FileStat

__all__ = [
    "CATEGORY_NAMES",
    "MAX_STAT_ENTRIES",
    "SIZE_BUCKETS",
    "STATS_TIME_BUDGET_SECONDS",
    "TOP_LARGEST",
    "build_stats",
    "category_of",
    "size_bucket_of",
]

#: Hard cap on classified entries; beyond it the walk stops and reports partial.
MAX_STAT_ENTRIES = 200_000

#: Wall-clock seconds one walk may spend; beyond it the walk stops and reports
#: partial.
STATS_TIME_BUDGET_SECONDS = 15

#: How many "largest files" the insights list keeps.
TOP_LARGEST = 10

#: Content categories, in display order. ``Other`` catches every extension the
#: named rules do not list, extension-less files included.
CATEGORY_NAMES: tuple[str, ...] = (
    "Code",
    "Documents",
    "Images",
    "Media",
    "Archives",
    "Data",
    "Other",
)

#: Lowercased extensions (without the dot) per named category.
CATEGORY_EXTENSIONS: dict[str, frozenset[str]] = {
    "Code": frozenset(
        (
            "py",
            "pyw",
            "pyi",
            "ipynb",
            "js",
            "mjs",
            "cjs",
            "ts",
            "tsx",
            "jsx",
            "c",
            "h",
            "cpp",
            "cxx",
            "hpp",
            "hh",
            "cc",
            "java",
            "cs",
            "go",
            "rs",
            "rb",
            "php",
            "swift",
            "kt",
            "kts",
            "scala",
            "sh",
            "bash",
            "zsh",
            "fish",
            "ps1",
            "bat",
            "cmd",
            "lua",
            "pl",
            "pm",
            "r",
            "jl",
            "sql",
            "html",
            "htm",
            "css",
            "scss",
            "sass",
            "less",
            "vue",
            "svelte",
            "astro",
        )
    ),
    "Documents": frozenset(
        (
            "md",
            "markdown",
            "rst",
            "adoc",
            "asciidoc",
            "txt",
            "text",
            "pdf",
            "doc",
            "docx",
            "odt",
            "rtf",
            "tex",
            "csv",
            "tsv",
            "xlsx",
            "xls",
            "pptx",
            "ppt",
            "epub",
            "mobi",
            "pages",
            "numbers",
        )
    ),
    "Images": frozenset(
        (
            "png",
            "jpg",
            "jpeg",
            "gif",
            "bmp",
            "svg",
            "webp",
            "ico",
            "icns",
            "tiff",
            "tif",
            "heic",
            "heif",
            "psd",
            "ai",
            "eps",
            "raw",
            "cr2",
            "nef",
        )
    ),
    "Media": frozenset(
        (
            "mp3",
            "wav",
            "flac",
            "ogg",
            "oga",
            "m4a",
            "aac",
            "wma",
            "opus",
            "mid",
            "midi",
            "mp4",
            "m4v",
            "mkv",
            "avi",
            "mov",
            "webm",
            "wmv",
            "flv",
            "mpg",
            "mpeg",
            "3gp",
        )
    ),
    "Archives": frozenset(
        (
            "zip",
            "tar",
            "gz",
            "tgz",
            "bz2",
            "tbz2",
            "xz",
            "txz",
            "7z",
            "rar",
            "zst",
            "iso",
            "dmg",
            "pkg",
            "jar",
            "war",
        )
    ),
    "Data": frozenset(
        (
            "json",
            "jsonl",
            "ndjson",
            "yaml",
            "yml",
            "toml",
            "xml",
            "ini",
            "cfg",
            "conf",
            "env",
            "db",
            "sqlite",
            "sqlite3",
            "parquet",
            "pkl",
            "pickle",
            "npy",
            "npz",
            "proto",
            "graphql",
        )
    ),
}

#: Size buckets, in ascending order: ``(label, lower bound)``. A file lands in
#: the last bucket whose lower bound is at most its size.
SIZE_BUCKETS: tuple[tuple[str, int], ...] = (
    ("0", 0),
    ("<1K", 1),
    ("1-10K", 1024),
    ("10-100K", 10 * 1024),
    ("100K-1M", 100 * 1024),
    ("1-10M", 1024 * 1024),
    ("10-100M", 10 * 1024 * 1024),
    ("100M-1G", 100 * 1024 * 1024),
    (">1G", 1024**3),
)


def category_of(suffix: str) -> str:
    """The content category of a lowercased file extension (no leading dot)."""
    for name in CATEGORY_NAMES[:-1]:
        if suffix in CATEGORY_EXTENSIONS[name]:
            return name
    return CATEGORY_NAMES[-1]


def size_bucket_of(size: int) -> str:
    """The histogram bucket ``size`` falls into."""
    label = SIZE_BUCKETS[0][0]
    for candidate, lower in SIZE_BUCKETS:
        if size >= lower:
            label = candidate
    return label


def suffix_of(name: str) -> str:
    """The lowercased extension of ``name`` without the dot; empty when none."""
    return Path(name).suffix.lstrip(".").casefold()


class _TreeAccumulator:
    """Mutable collector reduced into a frozen :class:`DirectoryStats` at the end.

    Only regular files contribute size, category, extension, bucket, permission
    and timeline data; directories and symlinks are counted, hidden or not, and
    symlinks are never followed.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.total_size = 0
        self.file_count = 0
        self.directory_count = 0
        self.hidden_count = 0
        self.symlink_count = 0
        self.scanned_entries = 0
        self.category_sizes: dict[str, int] = {name: 0 for name in CATEGORY_NAMES}
        self.extension_sizes: dict[str, int] = {}
        self.extension_counts: dict[str, int] = {}
        self.bucket_counts: dict[str, int] = {label: 0 for label, _ in SIZE_BUCKETS}
        self.permission_counts: dict[str, int] = {}
        self.daily_counts: dict[date, int] = {}
        self.monthly_counts: dict[str, int] = {}
        self._heap: list[tuple[int, int, FileStat]] = []
        self._serial = count()
        self._oldest: FileStat | None = None
        self._newest: FileStat | None = None

    def add_directory(self, name: str) -> None:
        """Count one directory entry."""
        self.scanned_entries += 1
        self.directory_count += 1
        if name.startswith("."):
            self.hidden_count += 1

    def add_symlink(self, name: str) -> None:
        """Count one symlink entry without reading through it."""
        self.scanned_entries += 1
        self.symlink_count += 1
        if name.startswith("."):
            self.hidden_count += 1

    def add_file(self, path: Path, name: str, info: os.stat_result) -> None:
        """Fold one regular file into every statistic."""
        self.scanned_entries += 1
        self.file_count += 1
        if name.startswith("."):
            self.hidden_count += 1
        size = info.st_size
        modified = info.st_mtime
        self.total_size += size
        suffix = suffix_of(name)
        self.category_sizes[category_of(suffix)] += size
        if suffix:
            self.extension_sizes[suffix] = self.extension_sizes.get(suffix, 0) + size
            self.extension_counts[suffix] = self.extension_counts.get(suffix, 0) + 1
        self.bucket_counts[size_bucket_of(size)] += 1
        mask = stat.filemode(info.st_mode)[1:]
        self.permission_counts[mask] = self.permission_counts.get(mask, 0) + 1
        day = datetime.fromtimestamp(modified).date()
        self.daily_counts[day] = self.daily_counts.get(day, 0) + 1
        month = day.strftime("%Y-%m")
        self.monthly_counts[month] = self.monthly_counts.get(month, 0) + 1
        self._offer_largest(FileStat(path=path, name=name, size=size, modified=modified))

    def _offer_largest(self, entry: FileStat) -> None:
        """Keep at most :data:`TOP_LARGEST` files, and the oldest/newest ones."""
        candidate = (entry.size, next(self._serial), entry)
        if len(self._heap) < TOP_LARGEST:
            heappush(self._heap, candidate)
        elif candidate[0] > self._heap[0][0]:
            heapreplace(self._heap, candidate)
        if self._oldest is None or entry.modified < self._oldest.modified:
            self._oldest = entry
        if self._newest is None or entry.modified > self._newest.modified:
            self._newest = entry

    def finish(self, *, partial: bool, elapsed: float) -> DirectoryStats:
        """Reduce the collected counters into the frozen result."""
        largest = tuple(entry for _, _, entry in sorted(self._heap, key=lambda item: -item[0]))
        return DirectoryStats(
            root=self.root,
            total_size=self.total_size,
            file_count=self.file_count,
            directory_count=self.directory_count,
            hidden_count=self.hidden_count,
            symlink_count=self.symlink_count,
            scanned_entries=self.scanned_entries,
            partial=partial,
            elapsed=elapsed,
            category_sizes=self.category_sizes,
            extension_sizes=self.extension_sizes,
            extension_counts=self.extension_counts,
            size_buckets=self.bucket_counts,
            permission_counts=self.permission_counts,
            daily_counts=self.daily_counts,
            monthly_counts=self.monthly_counts,
            largest=largest,
            oldest=self._oldest,
            newest=self._newest,
        )


def build_stats(
    root: str | os.PathLike[str],
    *,
    max_entries: int = MAX_STAT_ENTRIES,
    time_budget: float = STATS_TIME_BUDGET_SECONDS,
) -> DirectoryStats:
    """Walk ``root`` recursively and reduce it into a :class:`DirectoryStats`.

    The walk stops early -- with ``partial=True`` -- as soon as ``max_entries``
    entries have been classified or ``time_budget`` seconds have elapsed. The
    root itself is not counted as an entry. Entries that cannot be read are
    skipped; symlinks are counted but never followed or sized, so the walk
    cannot loop. A ``root`` that is not a directory yields empty statistics.
    """
    base = Path(root)
    started = monotonic()
    acc = _TreeAccumulator(base)
    partial = False
    if base.is_dir():
        for dirpath, dirnames, filenames in os.walk(base):
            stop = False
            for name in (*dirnames, *filenames):
                if acc.scanned_entries >= max_entries or monotonic() - started >= time_budget:
                    partial = True
                    stop = True
                    break
                entry = Path(dirpath) / name
                try:
                    info = os.lstat(entry)
                except OSError:
                    continue
                mode = info.st_mode
                if stat.S_ISLNK(mode):
                    acc.add_symlink(name)
                elif stat.S_ISDIR(mode):
                    acc.add_directory(name)
                else:
                    acc.add_file(entry, name, info)
            if stop:
                break
    return acc.finish(partial=partial, elapsed=monotonic() - started)
