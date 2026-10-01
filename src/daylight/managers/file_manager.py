"""Filesystem access for the UI. Must never import Textual or any widget code."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from daylight.managers import content, external
from daylight.models import DirEntry, FileEntry, FileProperties, ViewDocument

__all__ = [
    "SORT_KEYS",
    "FileManager",
    "OperationCancelled",
    "content",
    "external",
]

#: Logged when a delete cannot reach the OS trash and falls back to ``rm``.
#:
#: The fallback is never silent: a file the user asked to delete is still
#: deleted, but the loss of the trash safety net is written here, so a broken
#: trash integration cannot quietly turn every delete into a permanent one.
logger = logging.getLogger("daylight.managers.file_manager")

#: Sort keys offered by the Files page, in toggle order.
SORT_KEYS: tuple[str, ...] = ("name", "size", "time", "type")


def _is_hidden(path: Path) -> bool:
    """Return True when ``path.name`` is a dotfile."""
    return path.name.startswith(".")


def _kind_of(name: str, is_dir: bool, is_symlink: bool) -> str:
    """Classify an entry for icon selection and type sorting."""
    if is_dir:
        return "dir"
    if is_symlink:
        return "symlink"
    return "file"


def _suffix_of(name: str) -> str:
    """Return the lowercased extension of ``name`` without the leading dot."""
    return Path(name).suffix.lstrip(".").casefold()


class OperationCancelled(RuntimeError):
    """Raised inside a long copy/move/delete when the user pressed Cancel.

    Work already done is not undone: a cancelled batch stops between items and
    keeps whatever it finished, which is what the UI reports back.
    """


def _iter_files(root: Path) -> Iterator[Path]:
    """Yield every file under ``root`` in a stable order, or ``root`` if a file.

    Sorted, so a copy reports the same files in the same order every run and a
    progress bar that stops half way is reproducible.
    """
    if not root.is_dir():
        yield root
        return
    for parent, _dirs, files in os.walk(root):
        for name in sorted(files):
            yield Path(parent) / name


def _trash_timestamp() -> str:
    """Return the local timestamp the freedesktop trashinfo format expects."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _trash_on_windows(path: Path) -> bool:
    """Send ``path`` to the Recycle Bin through ``SHFileOperationW``.

    Returns False when the shell call is unavailable, so the caller can fall
    back to an explicitly logged ``rm`` rather than pretend the trash worked.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:  # pragma: no cover - not reachable off Windows
        return False
    fo_delete = 0x0003
    fof_allow_undo = 0x0040
    fof_no_confirmation = 0x0010
    fof_silent = 0x0004
    fof_noconfirmation_error = 0x0400
    try:  # pragma: no cover - not reachable off Windows

        class _SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        shell = ctypes.windll.shell32  # type: ignore[attr-defined]
        operation = _SHFILEOPSTRUCTW()
        operation.wFunc = fo_delete
        # Shell paths are double NUL terminated.
        operation.pFrom = str(path) + "\0\0"
        operation.pTo = None
        operation.fFlags = (
            fof_allow_undo | fof_no_confirmation | fof_silent | fof_noconfirmation_error
        )
        code = shell.SHFileOperationW(ctypes.byref(operation))
    except (AttributeError, OSError):  # pragma: no cover - not reachable off Windows
        return False
    return code == 0 and not operation.fAnyOperationsAborted


class _BatchProgress:
    """Turns per-item reports of a batch into a single whole-batch figure.

    A long operation reports per *item* when it walks a batch (the total is the
    number of entries), and per *file* when it has exactly one item to move (a
    single large directory then still shows movement). This adapter is only in
    the way of the batch case, and only there does it re-number the reports.
    """

    def __init__(self, progress: Callable[[int, int, Path], None] | None, total: int) -> None:
        self._progress = progress
        self._total = max(1, total)
        self._done = 0

    def item(self, _completed: int, _total: int, current: Path) -> None:
        """Publish one finished item as the next step of the batch."""
        if self._progress is None:
            return
        self._done = min(self._done + 1, self._total)
        self._progress(self._done, self._total, current)


def _check_cancelled(cancelled: Callable[[], bool] | None, current: Path) -> None:
    """Raise :class:`OperationCancelled` when ``cancelled`` says to stop."""
    if cancelled is not None and cancelled():
        raise OperationCancelled(f"cancelled while working on {current}")


class FileManager:
    """Filesystem access used by the UI modules, reads and mutations alike."""

    def __init__(self, show_hidden: bool = False) -> None:
        self.show_hidden = show_hidden

    # -- reading -----------------------------------------------------------

    def list_dir(self, path: str | os.PathLike[str]) -> list[FileEntry]:
        """List the entries of ``path``, directories first, hidden entries optional."""
        target = Path(path)
        entries = [self.stat(child) for child in target.iterdir()]
        if not self.show_hidden:
            entries = [entry for entry in entries if not entry.is_hidden]
        return sorted(entries, key=self._sort_key)

    def stat(self, path: str | os.PathLike[str]) -> FileEntry:
        """Return the :class:`FileEntry` describing ``path``."""
        target = Path(path)
        info = target.lstat()
        return FileEntry(
            name=target.name or str(target),
            path=target,
            is_dir=target.is_dir(),
            is_symlink=target.is_symlink(),
            size=info.st_size,
            modified=info.st_mtime,
            is_hidden=_is_hidden(target),
        )

    def iter_entries(self, path: str | os.PathLike[str]) -> Iterator[DirEntry]:
        """Yield the :class:`DirEntry` rows of ``path`` lazily.

        The directory is walked with a single ``os.scandir`` pass so that large
        directories stream into the table instead of being materialised first.
        Entries whose metadata cannot be read (broken symlinks, races) are
        skipped rather than aborting the listing.
        """
        target = Path(path)
        with os.scandir(target) as scan:
            for child in scan:
                entry = self._entry_from_dir_entry(child)
                if entry is None:
                    continue
                if entry.is_hidden and not self.show_hidden:
                    continue
                yield entry

    def permissions(self, path: str | os.PathLike[str]) -> str:
        """Return the ``rwxr-xr-x`` style permissions of ``path``."""
        return content.permission_string(Path(path).lstat().st_mode)

    # -- viewing and editing (DC-004) ---------------------------------------

    def view_kind(self, path: str | os.PathLike[str]) -> str:
        """Return how ``path`` should be viewed: md/json/text/binary/directory."""
        return content.view_kind(path)

    def load_view(
        self,
        path: str | os.PathLike[str],
        *,
        limit: int = content.PRETTY_LIMIT,
    ) -> ViewDocument:
        """Return the :class:`~daylight.models.ViewDocument` for ``path``.

        Thin delegate to :func:`daylight.managers.content.load_view` so the UI
        keeps talking to one manager for every disk read.
        """
        return content.load_view(path, limit=limit)

    def read_window(self, path: str | os.PathLike[str], start: int, count: int) -> list[str]:
        """Return a window of lines of ``path``, read lazily (never the whole file)."""
        return content.read_window(path, start, count)

    def count_lines(self, path: str | os.PathLike[str]) -> int | None:
        """Return how many lines ``path`` has, or None when it is too big to scan."""
        return content.count_lines(path)

    def find_matches(
        self,
        path: str | os.PathLike[str],
        needle: str,
        *,
        max_hits: int = 200,
    ) -> list[int]:
        """Return the 1-based line numbers of ``path`` containing ``needle``."""
        return content.find_matches(path, needle, max_hits=max_hits)

    def describe(
        self,
        path: str | os.PathLike[str],
        *,
        algorithm: str = content.CHECKSUM_ALGORITHM,
        with_checksum: bool = True,
    ) -> FileProperties:
        """Return the properties the detail view lists (stat, owner, MIME, checksum)."""
        return content.describe(path, algorithm=algorithm, with_checksum=with_checksum)

    def open_external(
        self,
        path: str | os.PathLike[str],
        *,
        runner: external.Runner | None = None,
    ) -> int:
        """Open ``path`` in the OS default application (binary files, AC4)."""
        return external.open_external(path, runner=runner)

    def describe_open(self, path: str | os.PathLike[str]) -> external.LaunchRequest:
        """Describe the external open of ``path``: target, app and argv (FR-403)."""
        return external.describe_open(path)

    def launch(
        self,
        request: external.LaunchRequest,
        *,
        runner: external.Runner | None = None,
    ) -> int:
        """Run a confirmed launch; return its exit status (FR-403).

        This is the only way a launch reaches the system: the UI describes a
        request, asks, and calls this with the answer, so the "asked first"
        property is enforced by there being no other way out.
        """
        return request.run(runner)

    def open_link(
        self,
        url: str,
        *,
        runner: external.Runner | None = None,
    ) -> int:
        """Open ``url`` in the default browser (FR-402)."""
        return external.open_link(url, runner=runner)

    def describe_link(self, url: str) -> external.LaunchRequest:
        """Describe opening ``url`` in the default browser, without running it."""
        return external.describe_link(url)

    def detect_url(self, text: str) -> str | None:
        """Return the first URL in ``text``, or None when there is none (FR-402)."""
        return external.detect_url(text)

    def url_in_file(
        self,
        path: str | os.PathLike[str],
        *,
        lines: int = 64,
    ) -> str | None:
        """Return the first URL in the head of a text file, or None (FR-402)."""
        return external.detect_url_in_file(path, lines=lines)

    def describe_edit(self, path: str | os.PathLike[str]) -> external.LaunchRequest:
        """Describe what editing ``path`` would launch, and with which app."""
        return external.describe_edit(external.edit_dispatch(path))

    def edit(
        self,
        path: str | os.PathLike[str],
        *,
        editor: str | None = None,
        runner: external.Runner | None = None,
    ) -> external.EditDispatch:
        """Edit ``path`` by type: text -> ``$EDITOR``, else the OS default app (AC5)."""
        dispatch = external.edit_dispatch(path, editor=editor)
        external.run(dispatch.command, runner)
        return dispatch

    def parent_of(self, path: str | os.PathLike[str]) -> Path:
        """Return the parent directory of ``path`` (path maths only, no disk read)."""
        target = Path(path)
        return target.parent if target.parent != target else target

    def can_ascend(self, path: str | os.PathLike[str]) -> bool:
        """True when ``path`` has a distinct parent directory to navigate to."""
        target = Path(path)
        return target.parent != target

    # -- creating -----------------------------------------------------------

    def create_file(self, directory: str | os.PathLike[str], name: str) -> Path:
        """Create an empty file called ``name`` in ``directory``; return its path."""
        return self._create(Path(directory), name, is_dir=False)

    def create_directory(self, directory: str | os.PathLike[str], name: str) -> Path:
        """Create a directory called ``name`` in ``directory``; return its path."""
        return self._create(Path(directory), name, is_dir=True)

    def create(
        self,
        directory: str | os.PathLike[str],
        name: str,
        *,
        is_dir: bool = False,
    ) -> Path:
        """Create a file (or a directory) called ``name``; return its path."""
        return self._create(Path(directory), name, is_dir=is_dir)

    def _create(self, directory: Path, name: str, *, is_dir: bool) -> Path:
        """Create one entry, refusing a name that could escape ``directory``."""
        clean = self._check_name(name)
        target = directory / clean
        if is_dir:
            target.mkdir()
        else:
            # ``x`` so a name that already exists fails loudly instead of
            # truncating whatever is there.
            target.touch(exist_ok=False)
        return target

    def _check_name(self, name: str) -> str:
        """Return ``name`` stripped, or raise ValueError when it is not usable."""
        clean = name.strip()
        if not clean:
            raise ValueError("a name is required")
        if clean in (".", ".."):
            raise ValueError(f"{clean!r} is not a usable name")
        if os.sep in clean or (os.altsep and os.altsep in clean) or "/" in clean:
            raise ValueError(f"{name!r} may not contain a path separator")
        return clean

    def unique_destination(self, directory: str | os.PathLike[str], name: str) -> Path:
        """Return a free path in ``directory`` for ``name``, appending ``-N``.

        Copying a file onto itself would either raise or overwrite, so a batch
        copy asks for a free name first and can therefore never clobber.
        """
        base = Path(directory)
        candidate = base / name
        if not candidate.exists():
            return candidate
        stem, dot, suffix = name.partition(".")
        counter = 1
        while True:
            number = f"-{counter}"
            candidate = base / f"{stem}{number}{dot}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    # -- copying ------------------------------------------------------------

    def copy(
        self,
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        *,
        progress: Callable[[int, int, Path], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        """Copy ``source`` to the exact path ``destination``; return the new path."""
        return self._copy_one(Path(source), Path(destination), progress, cancelled)

    def copy_into(
        self,
        sources: Iterable[str | os.PathLike[str]],
        directory: str | os.PathLike[str],
        *,
        progress: Callable[[int, int, Path], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> list[Path]:
        """Copy every entry of ``sources`` into ``directory``, never clobbering."""
        target_dir = Path(directory)
        target_dir.mkdir(parents=True, exist_ok=True)
        items = [Path(source) for source in sources]
        batch = _BatchProgress(progress, len(items))
        written: list[Path] = []
        for source in items:
            _check_cancelled(cancelled, source)
            target = self.unique_destination(target_dir, source.name)
            # A batch reports per item; a lone source reports per file, so
            # copying one large directory still moves the bar.
            single = len(items) <= 1
            written.append(
                self._copy_one(
                    source,
                    target,
                    progress if single else batch.item,
                    cancelled,
                    per_file=single,
                )
            )
        return written

    def _copy_one(
        self,
        source: Path,
        destination: Path,
        progress: Callable[[int, int, Path], None] | None,
        cancelled: Callable[[], bool] | None,
        *,
        per_file: bool = True,
    ) -> Path:
        """Copy one file or directory tree, reporting and cancelling per file.

        ``per_file`` is False for the batch case, where the caller numbers one
        step per source itself and the per-file reports would double count.
        """
        # Checked before anything is created, so a copy cancelled up front
        # leaves the destination exactly as it found it.
        _check_cancelled(cancelled, source)
        if not source.exists() and not source.is_symlink():
            raise FileNotFoundError(f"nothing to copy at {source}")
        if source.is_dir() and not source.is_symlink():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
        files = list(_iter_files(source))
        total = max(1, len(files))
        for index, item in enumerate(files, start=1):
            _check_cancelled(cancelled, source)
            target = destination / item.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if item.is_dir() and not item.is_symlink():
                target.mkdir(parents=True, exist_ok=True)
            else:
                shutil.copy2(item, target, follow_symlinks=False)
            if progress is not None and per_file:
                progress(index, total, item)
        if progress is not None and not per_file:
            progress(1, 1, destination)
        return destination

    # -- moving and renaming ------------------------------------------------

    def move(
        self,
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        *,
        progress: Callable[[int, int, Path], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        """Move ``source`` to the exact path ``destination``; return the new path."""
        origin = Path(source)
        target = Path(destination)
        _check_cancelled(cancelled, origin)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(origin), str(target))
        if progress is not None:
            progress(1, 1, target)
        return target

    def move_into(
        self,
        sources: Iterable[str | os.PathLike[str]],
        directory: str | os.PathLike[str],
        *,
        progress: Callable[[int, int, Path], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> list[Path]:
        """Move every entry of ``sources`` into ``directory``, never clobbering."""
        target_dir = Path(directory)
        target_dir.mkdir(parents=True, exist_ok=True)
        items = [Path(source) for source in sources]
        batch = _BatchProgress(progress, len(items))
        moved: list[Path] = []
        for source in items:
            _check_cancelled(cancelled, source)
            target = self.unique_destination(target_dir, source.name)
            moved.append(self.move(source, target, progress=batch.item))
        return moved

    def rename(self, path: str | os.PathLike[str], new_name: str) -> Path:
        """Rename ``path`` in place; return its new path.

        A rename that would land on an existing entry raises rather than
        destroying it.
        """
        source = Path(path)
        target = source.parent / self._check_name(new_name)
        if target == source:
            return target
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"{target} already exists")
        return self.move(source, target)

    # -- deleting -----------------------------------------------------------

    def trash_available(self) -> bool:
        """True when this platform can move a path to an OS trash.

        This is a read-only probe: asking whether a trash exists must never
        create one, so nothing is left behind by a listing or a cancelled run.

        ``False`` means a delete has to fall back to a permanent removal, which
        :meth:`delete` reports in the log before doing it.
        """
        if sys.platform == "win32":
            return True
        return self._trash_root().is_dir()

    def _trash_root(self) -> Path:
        """Return the trash directory this platform uses; create nothing.

        macOS keeps one flat ``~/.Trash`` and a deleted item has to sit directly
        in it for Finder to list it as trashed. Freedesktop instead nests the
        items in ``files/`` beside the ``info/`` restore records, and
        :meth:`trash` prepares those two directories when it moves something.
        """
        if sys.platform == "darwin":
            return Path.home() / ".Trash"
        data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        return Path(data_home) / "Trash"

    def trash(self, path: str | os.PathLike[str]) -> Path:
        """Move ``path`` into the OS trash; return where it went.

        Raises ``OSError`` when the trash cannot take it, so the caller decides
        what to do instead of the trash silently swallowing the delete.
        """
        source = Path(path).absolute()
        if sys.platform == "win32":
            if not _trash_on_windows(source):
                raise OSError(f"the Recycle Bin refused {source}")
            return source
        root = self._trash_root()
        if sys.platform == "darwin":
            # The macOS Trash is flat: no ``files/`` and no ``info/`` wrapper.
            target = self.unique_destination(root, source.name)
            shutil.move(str(source), str(target))
            return target
        files = root / "files"
        info = root / "info"
        try:
            files.mkdir(parents=True, exist_ok=True)
            info.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise OSError(f"cannot prepare the trash at {root}: {error}") from error
        target = self.unique_destination(files, source.name)
        shutil.move(str(source), str(target))
        # The freedesktop spec keeps restore information beside the file.
        (info / f"{target.name}.trashinfo").write_text(
            "[Trash Info]\n" f"Path={quote(str(source))}\n" f"DeletionDate={_trash_timestamp()}\n",
            encoding="utf-8",
        )
        return target

    def delete(
        self,
        path: str | os.PathLike[str],
        *,
        permanent: bool = False,
        progress: Callable[[int, int, Path], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        """Delete ``path``, via the OS trash unless ``permanent`` is set.

        When the platform has no trash the permanent removal still happens, but
        it is written to the log first: a delete the user asked for must not
        become a silent, unrecoverable one.
        """
        return self._delete_one(
            Path(path), permanent=permanent, progress=progress, cancelled=cancelled, per_file=True
        )

    def delete_many(
        self,
        paths: Iterable[str | os.PathLike[str]],
        *,
        permanent: bool = False,
        progress: Callable[[int, int, Path], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> list[Path]:
        """Delete every entry of ``paths``; return the paths that were removed."""
        items = [Path(path) for path in paths]
        batch = _BatchProgress(progress, len(items))
        return [
            self._delete_one(
                item,
                permanent=permanent,
                progress=batch.item,
                cancelled=cancelled,
                per_file=False,
            )
            for item in items
        ]

    def _delete_one(
        self,
        path: Path,
        *,
        permanent: bool,
        progress: Callable[[int, int, Path], None] | None,
        cancelled: Callable[[], bool] | None,
        per_file: bool,
    ) -> Path:
        """Trash one path, or remove it for good after saying so in the log.

        ``per_file`` is False for the batch case, where the caller numbers one
        step per path itself and the per-file reports would double count.
        """
        _check_cancelled(cancelled, path)
        if not path.exists() and not path.is_symlink():
            # Nothing to remove, so nothing to warn about: an already-deleted
            # path is a no-op, not a trash failure. It still finishes its step
            # so a batch can reach its end.
            if progress is not None:
                progress(1, 1, path)
            return path
        if not permanent:
            if self.trash_available():
                try:
                    trashed = self.trash(path)
                except OSError as error:
                    logger.warning("OS trash refused %s (%s); removing it instead", path, error)
                else:
                    if progress is not None:
                        progress(1, 1, trashed)
                    return trashed
            else:
                logger.warning(
                    "no OS trash available for %s: removing it permanently as an explicit fallback",
                    path,
                )
        self._remove(path, progress if per_file else None, cancelled)
        if progress is not None and not per_file:
            # With ``per_file`` the walk in ``_remove`` reported every step
            # already, and a second report would count one delete twice.
            progress(1, 1, path)
        return path

    def _remove(
        self,
        path: Path,
        progress: Callable[[int, int, Path], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> None:
        """Remove ``path`` for good, one file at a time so a cancel can land."""
        if not path.is_dir() or path.is_symlink():
            _check_cancelled(cancelled, path)
            path.unlink(missing_ok=True)
            if progress is not None:
                progress(1, 1, path)
            return
        entries = sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True)
        total = max(1, len(entries))
        for index, entry in enumerate(entries, start=1):
            _check_cancelled(cancelled, path)
            if entry.is_dir() and not entry.is_symlink():
                entry.rmdir()
            else:
                entry.unlink(missing_ok=True)
            if progress is not None:
                progress(index, total, entry)
        path.rmdir()

    # -- internals ---------------------------------------------------------

    def _entry_from_dir_entry(self, child: os.DirEntry[str]) -> DirEntry | None:
        """Convert one ``os.scandir`` result into a :class:`DirEntry`."""
        try:
            info = child.stat(follow_symlinks=False)
            is_symlink = child.is_symlink()
        except OSError:
            return None
        is_dir = child.is_dir(follow_symlinks=True)
        return DirEntry(
            name=child.name,
            path=Path(child.path),
            is_dir=is_dir,
            is_hidden=_is_hidden(Path(child.name)),
            is_symlink=is_symlink,
            size=info.st_size,
            modified=info.st_mtime,
            permissions=content.permission_string(info.st_mode),
            kind=_kind_of(child.name, is_dir, is_symlink),
            suffix=_suffix_of(child.name),
        )

    @staticmethod
    def _sort_key(entry: FileEntry) -> tuple[bool, str]:
        """Directories first, then case-insensitive by name."""
        return (not entry.is_dir, entry.name.casefold())
