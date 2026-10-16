"""Unit tests for the recursive statistics manager (DC-023).

Every test builds its own tree under ``tmp_path`` and pins the modification
times it cares about, so counts, buckets and timelines are deterministic.
"""

from __future__ import annotations

import dataclasses
import os
from datetime import datetime
from pathlib import Path

import pytest

from daylight.managers import stats
from daylight.managers.stats import (
    MAX_STAT_ENTRIES,
    SIZE_BUCKETS,
    STATS_TIME_BUDGET_SECONDS,
    build_stats,
    category_of,
    size_bucket_of,
)
from daylight.models import FileStat

#: Pinned modification time (2026-03-04 05:06:07 local) for the fixture tree.
MTIME = 1_772_601_567


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A tree with known sizes, extensions, one subdirectory and one symlink."""
    root = tmp_path / "tree"
    root.mkdir()
    files = {
        "readme.md": 100,
        "main.py": 1000,
        "photo.jpg": 2048,
        "notes.txt": 50,
        ".hidden": 10,
    }
    for name, size in files.items():
        target = root / name
        target.write_text("x" * size, encoding="utf-8")
        os.utime(target, (MTIME, MTIME))
    (root / "sub").mkdir()
    os.utime(root / "sub", (MTIME, MTIME))
    (root / "sub" / "deep.py").write_text("d" * 24, encoding="utf-8")
    os.utime(root / "sub" / "deep.py", (MTIME, MTIME))
    os.symlink(root / "readme.md", root / "link.md")
    return root


def pinned_day() -> datetime:
    """The pinned mtime as a local datetime, the way the manager buckets it."""
    return datetime.fromtimestamp(MTIME)


# -- summary counts ----------------------------------------------------------


def test_summary_counts(tree: Path) -> None:
    """Files, directories, symlinks, hidden entries and bytes are all counted."""
    result = build_stats(tree)
    assert result.root == tree
    assert result.file_count == 6
    assert result.directory_count == 1
    assert result.symlink_count == 1
    assert result.hidden_count == 1
    assert result.total_size == 100 + 1000 + 2048 + 50 + 10 + 24
    assert result.scanned_entries == 8
    assert result.partial is False
    assert result.dirs_files_ratio == "1:6"


def test_the_root_itself_is_not_an_entry(tmp_path: Path) -> None:
    """A directory with nothing in it has zero of everything."""
    root = tmp_path / "empty"
    root.mkdir()
    result = build_stats(root)
    assert result.file_count == 0
    assert result.directory_count == 0
    assert result.scanned_entries == 0
    assert result.total_size == 0
    assert result.partial is False
    assert result.largest == ()
    assert result.oldest is None
    assert result.newest is None


def test_a_missing_root_yields_empty_stats(tmp_path: Path) -> None:
    result = build_stats(tmp_path / "nope")
    assert result.file_count == 0
    assert result.partial is False


def test_symlinks_are_counted_but_never_followed_or_sized(tree: Path) -> None:
    """The link counts once; its target is not double-counted through it."""
    result = build_stats(tree)
    assert result.symlink_count == 1
    assert result.total_size == 3232


# -- categories and extensions -------------------------------------------------


def test_category_sizes(tree: Path) -> None:
    result = build_stats(tree)
    assert result.category_sizes["Code"] == 1000 + 24
    assert result.category_sizes["Documents"] == 100 + 50
    assert result.category_sizes["Images"] == 2048
    assert result.category_sizes["Other"] == 10
    assert result.category_sizes["Media"] == 0
    # Every byte lands in exactly one category.
    assert sum(result.category_sizes.values()) == result.total_size


def test_extension_sizes_and_counts(tree: Path) -> None:
    result = build_stats(tree)
    assert result.extension_sizes == {"md": 100, "py": 1024, "jpg": 2048, "txt": 50}
    assert result.extension_counts == {"md": 1, "py": 2, "jpg": 1, "txt": 1}


def test_category_of() -> None:
    assert category_of("py") == "Code"
    assert category_of("md") == "Documents"
    assert category_of("png") == "Images"
    assert category_of("mp4") == "Media"
    assert category_of("zip") == "Archives"
    assert category_of("json") == "Data"
    assert category_of("weird") == "Other"
    assert category_of("") == "Other"


# -- size histogram --------------------------------------------------------------


def test_size_buckets(tree: Path) -> None:
    result = build_stats(tree)
    assert result.size_buckets["0"] == 0
    assert result.size_buckets["<1K"] == 5  # 100, 50, 10, 24, 1000 bytes
    assert result.size_buckets["1-10K"] == 1  # 2048 bytes
    assert sum(result.size_buckets.values()) == result.file_count
    assert tuple(result.size_buckets) == tuple(label for label, _ in SIZE_BUCKETS)


def test_size_bucket_of() -> None:
    assert size_bucket_of(0) == "0"
    assert size_bucket_of(1) == "<1K"
    assert size_bucket_of(1023) == "<1K"
    assert size_bucket_of(1024) == "1-10K"
    assert size_bucket_of(1024**2) == "1-10M"
    assert size_bucket_of(1024**3) == ">1G"


# -- permissions -------------------------------------------------------------


def test_permission_masks(tmp_path: Path) -> None:
    """The nine-character mask of every regular file is counted."""
    root = tmp_path / "modes"
    root.mkdir()
    private = root / "secret.txt"
    private.write_text("s", encoding="utf-8")
    private.chmod(0o600)
    shared = root / "open.sh"
    shared.write_text("#!/bin/sh\n", encoding="utf-8")
    shared.chmod(0o755)
    result = build_stats(root)
    assert result.permission_counts == {"rw-------": 1, "rwxr-xr-x": 1}


def test_default_masks_are_counted(tree: Path) -> None:
    result = build_stats(tree)
    assert result.permission_counts == {"rw-r--r--": 6}


# -- modification timeline ------------------------------------------------------


def test_daily_and_monthly_counts(tree: Path) -> None:
    result = build_stats(tree)
    day = pinned_day().date()
    assert result.daily_counts == {day: 6}
    assert result.monthly_counts == {day.strftime("%Y-%m"): 6}


# -- insights ---------------------------------------------------------------


def test_insights_rank_oldest_and_newest(tmp_path: Path) -> None:
    root = tmp_path / "ages"
    root.mkdir()
    plan = {"a.txt": (10, 1_000_000), "b.txt": (20, 2_000_000), "c.txt": (30, 1_500_000)}
    for name, (size, mtime) in plan.items():
        target = root / name
        target.write_text("x" * size, encoding="utf-8")
        os.utime(target, (mtime, mtime))
    result = build_stats(root)
    assert [entry.name for entry in result.largest] == ["c.txt", "b.txt", "a.txt"]
    assert result.oldest is not None and result.oldest.name == "a.txt"
    assert result.newest is not None and result.newest.name == "b.txt"


def test_top_largest_is_capped_at_ten(tmp_path: Path) -> None:
    root = tmp_path / "many"
    root.mkdir()
    for index in range(12):
        target = root / f"f{index:02d}.txt"
        target.write_text("x" * ((index + 1) * 10), encoding="utf-8")
    result = build_stats(root)
    assert len(result.largest) == 10
    assert result.largest[0].size == 120
    assert min(entry.size for entry in result.largest) == 30


def test_insight_entries_are_file_stats(tree: Path) -> None:
    result = build_stats(tree)
    assert result.largest[0].name == "photo.jpg"
    assert isinstance(result.largest[0], FileStat)
    assert result.largest[0].path == tree / "photo.jpg"


# -- safety caps (AC5) ---------------------------------------------------------


def test_the_entry_cap_marks_partial(tmp_path: Path) -> None:
    root = tmp_path / "capped"
    root.mkdir()
    for index in range(5):
        (root / f"f{index}.txt").write_text("x", encoding="utf-8")
    result = build_stats(root, max_entries=3)
    assert result.partial is True
    assert result.scanned_entries == 3
    assert result.file_count == 3


def test_the_time_budget_marks_partial(tmp_path: Path) -> None:
    root = tmp_path / "slow"
    root.mkdir()
    (root / "f.txt").write_text("x", encoding="utf-8")
    result = build_stats(root, time_budget=0.0)
    assert result.partial is True
    assert result.scanned_entries == 0
    assert result.file_count == 0


def test_the_shipped_cap_values() -> None:
    assert MAX_STAT_ENTRIES == 200_000
    assert STATS_TIME_BUDGET_SECONDS == 15


# -- models ----------------------------------------------------------------


def test_directory_stats_is_frozen(tree: Path) -> None:
    result = build_stats(tree)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.file_count = 0  # type: ignore[misc]


def test_file_stat_is_frozen() -> None:
    entry = FileStat(path=Path("/tmp/a"), name="a", size=1, modified=0.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.size = 2  # type: ignore[misc]


def test_stats_manager_never_imports_textual() -> None:
    """ADR-003/ADR-013: the manager is pure filesystem work, no UI imports."""
    source = Path(stats.__file__).read_text(encoding="utf-8")
    assert "import textual" not in source
    assert "from textual" not in source
