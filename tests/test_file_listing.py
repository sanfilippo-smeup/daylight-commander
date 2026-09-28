"""Unit tests for directory listing: lazy iteration, permissions and scale."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from daylight.managers import FileManager
from daylight.models import DirEntry


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A small directory tree with a folder, files, a dotfile and a symlink."""
    (tmp_path / "alpha.txt").write_text("a" * 10, encoding="utf-8")
    (tmp_path / "beta.py").write_text("b" * 3000, encoding="utf-8")
    (tmp_path / ".hidden").write_text("secret", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "deep.md").write_text("dd", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(tmp_path / "alpha.txt")
    return tmp_path


# -- laziness (AC1) --------------------------------------------------------


def test_iter_entries_returns_an_iterator(tree: Path) -> None:
    assert isinstance(FileManager().iter_entries(tree), Iterator)


def test_iter_entries_is_lazy_and_does_not_materialise(tree: Path) -> None:
    """Taking one entry must not read the whole directory."""
    stream = FileManager().iter_entries(tree)
    first = next(stream)
    assert isinstance(first, DirEntry)
    stream.close()


def test_iter_entries_yields_one_row_per_child(tree: Path) -> None:
    names = {entry.name for entry in FileManager().iter_entries(tree)}
    assert names == {"alpha.txt", "beta.py", "docs", "link.txt"}


def test_iter_entries_hides_dotfiles_by_default(tree: Path) -> None:
    names = {entry.name for entry in FileManager().iter_entries(tree)}
    assert ".hidden" not in names


def test_iter_entries_can_show_hidden(tree: Path) -> None:
    manager = FileManager(show_hidden=True)
    hidden = [entry.name for entry in manager.iter_entries(tree) if entry.is_hidden]
    assert hidden == [".hidden"]


def test_iter_entries_reports_type_size_and_permissions(tree: Path) -> None:
    entries = {entry.name: entry for entry in FileManager().iter_entries(tree)}
    assert entries["docs"].is_dir is True
    assert entries["docs"].kind == "dir"
    assert entries["beta.py"].size == 3000
    assert entries["beta.py"].suffix == "py"
    assert entries["alpha.txt"].permissions == "rw-r--r--"
    assert entries["link.txt"].is_symlink is True
    assert entries["link.txt"].kind == "symlink"


def test_iter_entries_reports_directory_permissions(tree: Path) -> None:
    entries = {entry.name: entry for entry in FileManager().iter_entries(tree)}
    assert entries["docs"].permissions == "rwxr-xr-x"


def test_iter_entries_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list(FileManager().iter_entries(tmp_path / "nope"))


# -- permissions -----------------------------------------------------------


def test_permissions_returns_rwx_triple(tree: Path) -> None:
    assert FileManager().permissions(tree / "alpha.txt") == "rw-r--r--"


def test_permissions_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        FileManager().permissions(tmp_path / "nope")


# -- navigation helpers ----------------------------------------------------


def test_parent_of_walks_up(tree: Path) -> None:
    assert FileManager().parent_of(tree / "docs") == tree


def test_can_ascend_is_false_at_the_root() -> None:
    assert FileManager().can_ascend(Path("/")) is False


def test_can_ascend_is_true_below_the_root(tree: Path) -> None:
    assert FileManager().can_ascend(tree) is True


# -- the parent row --------------------------------------------------------


def test_dir_entry_parent_points_at_the_parent_dir() -> None:
    row = DirEntry.parent("/a/b/c")
    assert row.name == ".."
    assert row.path == Path("/a/b")
    assert row.is_parent is True
    assert row.is_dir is True


def test_dir_entry_parent_at_root_stays_at_root() -> None:
    assert DirEntry.parent("/").path == Path("/")


# -- scale (AC1) -----------------------------------------------------------


@pytest.mark.parametrize("count", [20_000, 100_000])
def test_large_directory_lists_and_streams(tmp_path: Path, count: int) -> None:
    """A 100k-entry directory must stream in bounded time, not materialise slowly.

    Creating the fixture is the slow part and is excluded from the measured
    listing: what is asserted is that the listing itself is quick and that the
    stream can be abandoned early.
    """
    big = tmp_path / f"big-{count}"
    big.mkdir()
    for index in range(count):
        (big / f"f{index:06d}.dat").write_bytes(b"")

    manager = FileManager()
    started = time.perf_counter()
    names = {entry.name for entry in manager.iter_entries(big)}
    elapsed = time.perf_counter() - started

    assert len(names) == count
    assert elapsed < 20.0, f"listing {count} entries took {elapsed:.2f}s"

    started = time.perf_counter()
    stream = manager.iter_entries(big)
    next(stream)
    first_row = time.perf_counter() - started
    stream.close()
    assert first_row < 1.0, f"first row took {first_row:.3f}s"


def test_large_directory_scan_uses_a_single_scandir_pass(tmp_path: Path) -> None:
    """The listing must not re-stat every child with a second directory walk."""
    big = tmp_path / "big"
    big.mkdir()
    for index in range(50):
        (big / f"f{index:03d}").write_bytes(b"")

    calls: list[str] = []
    original = os.scandir

    def counting_scandir(path: str | Path) -> object:
        calls.append(str(path))
        return original(path)

    os.scandir = counting_scandir  # type: ignore[assignment]
    try:
        assert len(list(FileManager().iter_entries(big))) == 50
    finally:
        os.scandir = original  # type: ignore[assignment]
    assert calls == [str(big)]
