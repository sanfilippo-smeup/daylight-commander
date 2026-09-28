"""Manager-side file operations (DC-003 AC1, AC3, AC5), against a temp dir.

Everything here drives :class:`daylight.managers.FileManager` directly, so the
acceptance criteria are checked without a TUI: copy/move/rename/delete/create
really happen on disk, a long run really reports progress and really stops when
it is cancelled, and a delete really lands in a trash the test owns.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import pytest

from daylight.managers import FileManager, OperationCancelled

#: The logger that must carry the ``rm`` fallback warning (the safety rule).
LOGGER = "daylight.managers.file_manager"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A source directory with a file, a subdirectory and a nested file."""
    source = tmp_path / "src"
    (source / "docs").mkdir(parents=True)
    (source / "alpha.txt").write_text("alpha", encoding="utf-8")
    (source / "docs" / "deep.md").write_text("deep", encoding="utf-8")
    return source


@pytest.fixture
def target(tmp_path: Path) -> Path:
    """An empty destination directory."""
    destination = tmp_path / "dst"
    destination.mkdir()
    return destination


# -- AC1: copy ---------------------------------------------------------------


def test_copy_a_file_to_an_exact_path(tree: Path, target: Path) -> None:
    copied = FileManager().copy(tree / "alpha.txt", target / "copy.txt")
    assert copied == target / "copy.txt"
    assert copied.read_text(encoding="utf-8") == "alpha"
    assert (tree / "alpha.txt").exists(), "copy must leave the original alone"


def test_copy_a_directory_copies_the_whole_tree(tree: Path, target: Path) -> None:
    copied = FileManager().copy(tree, target / "src")
    assert (copied / "alpha.txt").read_text(encoding="utf-8") == "alpha"
    assert (copied / "docs" / "deep.md").read_text(encoding="utf-8") == "deep"
    assert (tree / "docs" / "deep.md").exists()


def test_copy_preserves_the_file_mode(tree: Path, target: Path) -> None:
    (tree / "alpha.txt").chmod(0o640)
    copied = FileManager().copy(tree / "alpha.txt", target / "copy.txt")
    assert copied.stat().st_mode & 0o777 == 0o640


def test_copy_into_batches_and_never_clobbers(tree: Path, target: Path) -> None:
    (target / "alpha.txt").write_text("existing", encoding="utf-8")
    written = FileManager().copy_into([tree / "alpha.txt", tree / "docs"], target, progress=None)
    assert [item.name for item in written] == ["alpha-1.txt", "docs"]
    assert (target / "alpha.txt").read_text(encoding="utf-8") == "existing"
    assert (target / "alpha-1.txt").read_text(encoding="utf-8") == "alpha"
    assert (target / "docs" / "deep.md").exists()


def test_copy_into_creates_a_missing_destination(tree: Path, tmp_path: Path) -> None:
    missing = tmp_path / "made" / "up"
    written = FileManager().copy_into([tree / "alpha.txt"], missing)
    assert written == [missing / "alpha.txt"]
    assert written[0].exists()


def test_copy_of_a_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        FileManager().copy(tmp_path / "nope.txt", tmp_path / "out.txt")


def test_unique_destination_walks_the_counter(tmp_path: Path) -> None:
    manager = FileManager()
    first = manager.unique_destination(tmp_path, "a.txt")
    assert first == tmp_path / "a.txt"
    first.touch()
    assert manager.unique_destination(tmp_path, "a.txt") == tmp_path / "a-1.txt"
    (tmp_path / "a-1.txt").touch()
    assert manager.unique_destination(tmp_path, "a.txt") == tmp_path / "a-2.txt"


# -- AC1: move and rename ----------------------------------------------------


def test_move_a_file(tree: Path, target: Path) -> None:
    moved = FileManager().move(tree / "alpha.txt", target / "moved.txt")
    assert moved.read_text(encoding="utf-8") == "alpha"
    assert not (tree / "alpha.txt").exists()


def test_move_a_directory(tree: Path, target: Path) -> None:
    moved = FileManager().move(tree / "docs", target / "docs")
    assert (moved / "deep.md").exists()
    assert not (tree / "docs").exists()


def test_move_into_batches(tree: Path, target: Path) -> None:
    (target / "alpha.txt").write_text("existing", encoding="utf-8")
    moved = FileManager().move_into([tree / "alpha.txt", tree / "docs"], target)
    assert [item.name for item in moved] == ["alpha-1.txt", "docs"]
    assert not (tree / "alpha.txt").exists()
    assert (target / "alpha.txt").read_text(encoding="utf-8") == "existing"


def test_rename_keeps_the_entry_in_place(tree: Path) -> None:
    renamed = FileManager().rename(tree / "alpha.txt", "beta.txt")
    assert renamed == tree / "beta.txt"
    assert renamed.read_text(encoding="utf-8") == "alpha"
    assert not (tree / "alpha.txt").exists()


def test_rename_to_the_same_name_is_a_no_op(tree: Path) -> None:
    assert FileManager().rename(tree / "alpha.txt", "alpha.txt") == tree / "alpha.txt"


def test_rename_onto_an_existing_name_refuses(tree: Path) -> None:
    (tree / "beta.txt").write_text("beta", encoding="utf-8")
    with pytest.raises(FileExistsError):
        FileManager().rename(tree / "alpha.txt", "beta.txt")
    assert (tree / "beta.txt").read_text(encoding="utf-8") == "beta", "beta must survive"
    assert (tree / "alpha.txt").exists(), "alpha must survive a refused rename"


@pytest.mark.parametrize("name", ["", "   ", ".", "..", "sub/dir.txt"])
def test_rename_rejects_a_name_that_could_escape(tree: Path, name: str) -> None:
    with pytest.raises(ValueError):
        FileManager().rename(tree / "alpha.txt", name)
    assert (tree / "alpha.txt").exists()


# -- AC1: create -------------------------------------------------------------


def test_create_file(tree: Path) -> None:
    created = FileManager().create_file(tree, "new.txt")
    assert created == tree / "new.txt"
    assert created.read_text(encoding="utf-8") == ""


def test_create_directory(tree: Path) -> None:
    created = FileManager().create_directory(tree, "newdir")
    assert created.is_dir()


def test_create_dispatches_on_is_dir(tree: Path) -> None:
    assert FileManager().create(tree, "f.txt").is_file()
    assert FileManager().create(tree, "d", is_dir=True).is_dir()


def test_create_over_an_existing_entry_refuses(tree: Path) -> None:
    with pytest.raises(FileExistsError):
        FileManager().create_file(tree, "alpha.txt")
    assert (tree / "alpha.txt").read_text(encoding="utf-8") == "alpha"


# -- AC5: delete goes to the trash -------------------------------------------


def trashed_dir(root: Path) -> Path:
    """Where ``FileManager.trash`` puts an item on the running platform.

    macOS uses one flat ``~/.Trash``; freedesktop nests items in ``files/``.
    """
    return root if sys.platform == "darwin" else root / "files"


@pytest.fixture
def fake_trash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The trash directory the test owns, so nothing real is touched."""
    root = tmp_path / "Trash"
    root.mkdir(parents=True)
    if sys.platform != "darwin":
        (root / "files").mkdir()
        (root / "info").mkdir()
    monkeypatch.setattr(FileManager, "_trash_root", lambda self: root)
    return trashed_dir(root)


def test_delete_moves_a_file_into_the_trash(tree: Path, fake_trash: Path) -> None:
    removed = FileManager().delete(tree / "alpha.txt")
    assert not (tree / "alpha.txt").exists()
    assert removed == fake_trash / "alpha.txt"
    assert removed.read_text(encoding="utf-8") == "alpha", "the trash keeps the content"


@pytest.mark.skipif(sys.platform != "darwin", reason="the flat macOS Trash layout")
def test_the_macos_trash_is_flat_so_finder_sees_the_item(tree: Path, fake_trash: Path) -> None:
    """A macOS item has to sit directly in ``~/.Trash``, not under ``files/``."""
    removed = FileManager().delete(tree / "alpha.txt")
    assert removed.parent == fake_trash, "the flat Trash takes the item directly"
    assert not removed.parent.parent.joinpath("files").exists()
    assert not removed.parent.parent.joinpath("info").exists()


def test_delete_moves_a_whole_directory_into_the_trash(tree: Path, fake_trash: Path) -> None:
    FileManager().delete(tree / "docs")
    assert not (tree / "docs").exists()
    assert (fake_trash / "docs" / "deep.md").read_text(encoding="utf-8") == "deep"


def test_delete_gives_a_trashed_name_a_free_one(tree: Path, fake_trash: Path) -> None:
    (fake_trash / "alpha.txt").write_text("older", encoding="utf-8")
    removed = FileManager().delete(tree / "alpha.txt")
    assert removed.name == "alpha-1.txt"
    assert (fake_trash / "alpha.txt").read_text(encoding="utf-8") == "older"


def test_delete_is_idempotent_on_a_missing_path(tmp_path: Path, fake_trash: Path) -> None:
    assert FileManager().delete(tmp_path / "never-existed") == tmp_path / "never-existed"


def test_deleting_a_missing_path_warns_about_nothing(
    tree: Path, fake_trash: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A no-op is not a trash failure, so it must not log one."""
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        FileManager().delete(tree / "never-existed")
    assert [record.getMessage() for record in caplog.records] == []


@pytest.mark.skipif(sys.platform == "win32", reason="the Recycle Bin is always on Windows")
def test_trash_available_creates_no_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asking whether a trash exists must not build one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert FileManager().trash_available() is False
    assert list(home.iterdir()) == [], "the probe left no trash behind"
    assert not (tmp_path / "data").exists()


def test_permanent_delete_bypasses_the_trash(tree: Path, fake_trash: Path) -> None:
    removed = FileManager().delete(tree / "alpha.txt", permanent=True)
    assert removed == tree / "alpha.txt"
    assert not (tree / "alpha.txt").exists()
    assert not (fake_trash / "alpha.txt").exists(), "trash must be untouched"


def test_permanent_delete_removes_a_whole_tree(tree: Path, fake_trash: Path) -> None:
    FileManager().delete(tree / "docs", permanent=True)
    assert not (tree / "docs").exists()
    assert (tree / "alpha.txt").exists(), "a sibling must survive"
    assert not (fake_trash / "docs").exists(), "trash must be untouched"
    assert list(fake_trash.iterdir()) == []


def test_delete_many_batches_into_the_trash(tree: Path, fake_trash: Path) -> None:
    removed = FileManager().delete_many([tree / "alpha.txt", tree / "docs"])
    assert [item.name for item in removed] == ["alpha.txt", "docs"]
    assert {item.name for item in fake_trash.iterdir()} == {"alpha.txt", "docs"}
    assert (tree / "alpha.txt").exists() is False


def test_delete_many_permanent_removes_only_the_listed_paths(tree: Path, fake_trash: Path) -> None:
    FileManager().delete_many([tree / "docs"], permanent=True)
    assert not (tree / "docs").exists()
    assert (tree / "alpha.txt").exists()


# -- the safety rule: the ``rm`` fallback is explicit and logged --------------


def test_without_a_trash_the_fallback_is_logged(
    tree: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(FileManager, "trash_available", lambda self: False)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        FileManager().delete(tree / "alpha.txt")
    assert not (tree / "alpha.txt").exists()
    messages = [record.getMessage() for record in caplog.records]
    assert any("no OS trash available" in message for message in messages), messages
    assert any("permanently" in message for message in messages), messages


def test_a_trash_that_refuses_is_reported_before_the_fallback(
    tree: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(self: FileManager, path: Path) -> Path:
        raise OSError("trash is broken")

    monkeypatch.setattr(FileManager, "trash", refuse)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        FileManager().delete(tree / "alpha.txt")
    assert not (tree / "alpha.txt").exists()
    messages = [record.getMessage() for record in caplog.records]
    assert any("OS trash refused" in message for message in messages), messages


def test_permanent_delete_never_mentions_the_trash(
    tree: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``permanent=True`` is the explicit action, so it logs no fallback."""
    monkeypatch.setattr(FileManager, "trash_available", lambda self: False)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        FileManager().delete(tree / "alpha.txt", permanent=True)
    assert [record.getMessage() for record in caplog.records] == []


# -- AC3: progress and cancel -----------------------------------------------


def build_many(root: Path, count: int) -> Path:
    """A directory of ``count`` small files, enough to cancel part way through."""
    big = root / "big"
    big.mkdir(parents=True)
    for index in range(count):
        (big / f"f{index:04d}.txt").write_text("x" * 64, encoding="utf-8")
    return big


def test_copy_reports_progress_per_file(tmp_path: Path) -> None:
    source = build_many(tmp_path, 12)
    seen: list[tuple[int, int, Path]] = []
    FileManager().copy(source, tmp_path / "copy", progress=lambda *args: seen.append(args))
    assert len(seen) == 12
    assert seen[0][0] == 1
    assert seen[-1] == (12, 12, source / "f0011.txt")
    assert all(done <= total for done, total, _ in seen)


def test_copy_into_reports_progress_over_the_whole_batch(tmp_path: Path) -> None:
    first = build_many(tmp_path / "one", 3)
    second = tmp_path / "two"
    second.mkdir()
    (second / "other.txt").write_text("o", encoding="utf-8")
    seen: list[tuple[int, int, Path]] = []
    FileManager().copy_into(
        [first, second / "other.txt"], tmp_path / "out", progress=lambda *args: seen.append(args)
    )
    assert [done for done, _, _ in seen] == [1, 2]
    assert {total for _, total, _ in seen} == {2}


def test_a_cancelled_copy_raises_and_keeps_what_it_finished(tmp_path: Path) -> None:
    source = build_many(tmp_path, 10)
    seen: list[Path] = []
    cancel = threading.Event()

    def progress(_done: int, _total: int, current: Path) -> None:
        seen.append(current)
        if len(seen) == 4:
            cancel.set()

    with pytest.raises(OperationCancelled):
        FileManager().copy(source, tmp_path / "copy", progress=progress, cancelled=cancel.is_set)
    copied = sorted(item.name for item in (tmp_path / "copy").iterdir())
    assert len(copied) == 4, f"cancel must stop between files, got {copied}"
    assert (tmp_path / "copy" / "f0000.txt").exists()
    assert not (tmp_path / "copy" / "f0009.txt").exists()


def test_a_copy_cancelled_before_it_starts_touches_nothing(tmp_path: Path) -> None:
    source = build_many(tmp_path, 3)
    with pytest.raises(OperationCancelled):
        FileManager().copy(source, tmp_path / "copy", cancelled=lambda: True)
    assert not (tmp_path / "copy").exists()


def test_a_cancelled_batch_stops_at_the_next_item(tree: Path, target: Path) -> None:
    (tree / "beta.txt").write_text("beta", encoding="utf-8")
    sources = [tree / "alpha.txt", tree / "beta.txt", tree / "docs"]
    seen: list[Path] = []
    cancel = threading.Event()

    def progress(_done: int, _total: int, current: Path) -> None:
        seen.append(current)
        cancel.set()

    with pytest.raises(OperationCancelled):
        FileManager().move_into(sources, target, progress=progress, cancelled=cancel.is_set)
    assert len(seen) == 1, "only the first item ran"
    assert (target / "alpha.txt").exists(), "the item that finished stays moved"
    assert (tree / "beta.txt").exists(), "the item the cancel caught never started"
    assert (tree / "docs").exists()


def test_a_cancelled_delete_stops_before_the_next_path(tree: Path, fake_trash: Path) -> None:
    (tree / "beta.txt").write_text("beta", encoding="utf-8")
    cancel = threading.Event()

    def progress(_done: int, _total: int, _current: Path) -> None:
        cancel.set()

    with pytest.raises(OperationCancelled):
        FileManager().delete_many(
            [tree / "alpha.txt", tree / "beta.txt"],
            progress=progress,
            cancelled=cancel.is_set,
        )
    assert not (tree / "alpha.txt").exists()
    assert (tree / "beta.txt").exists(), "the second delete never ran"


def test_a_cancelled_permanent_delete_of_a_tree_keeps_the_rest(tmp_path: Path) -> None:
    source = build_many(tmp_path, 20)
    seen: list[int] = []
    cancel = threading.Event()

    def progress(done: int, _total: int, _current: Path) -> None:
        seen.append(done)
        if len(seen) == 5:
            cancel.set()

    with pytest.raises(OperationCancelled):
        FileManager().delete(source, permanent=True, progress=progress, cancelled=cancel.is_set)
    survivors = sorted(item.name for item in source.iterdir())
    assert len(survivors) == 15, f"cancel must land between files, got {survivors}"


def test_a_permanent_delete_reports_each_step_once(tree: Path) -> None:
    """The walk reports the steps; the delete must not report them again."""
    seen: list[tuple[int, int, Path]] = []
    FileManager().delete(tree / "docs", permanent=True, progress=lambda *args: seen.append(args))
    assert seen == [(1, 1, tree / "docs" / "deep.md")], f"one report per step, got {seen}"


def test_a_permanent_delete_of_one_file_reports_once(tree: Path) -> None:
    seen: list[tuple[int, int, Path]] = []
    FileManager().delete(
        tree / "alpha.txt", permanent=True, progress=lambda *args: seen.append(args)
    )
    assert seen == [(1, 1, tree / "alpha.txt")], f"one file is one step, got {seen}"


def test_progress_is_optional_for_every_operation(tree: Path, target: Path) -> None:
    """No callback and no cancel flag is the normal, cheapest call."""
    manager = FileManager()
    manager.copy(tree / "alpha.txt", target / "a.txt")
    manager.move(target / "a.txt", target / "b.txt")
    manager.delete(target / "b.txt", permanent=True)
    assert (tree / "alpha.txt").exists()
    assert not (target / "b.txt").exists()
