"""Filesystem access for the UI. Must never import Textual or any widget code."""

from __future__ import annotations

import os
import stat as stat_module
from collections.abc import Iterator
from pathlib import Path

from daylight.models import DirEntry, FileEntry

__all__ = ["SORT_KEYS", "FileManager"]

#: Sort keys offered by the Files page, in toggle order.
SORT_KEYS: tuple[str, ...] = ("name", "size", "time", "type")

_PERMISSION_CHARS = ("r", "w", "x")

#: The user/group/other read-write-execute bit triples, in display order.
_PERMISSION_TRIOS = (
    (stat_module.S_IRUSR, stat_module.S_IWUSR, stat_module.S_IXUSR),
    (stat_module.S_IRGRP, stat_module.S_IWGRP, stat_module.S_IXGRP),
    (stat_module.S_IROTH, stat_module.S_IWOTH, stat_module.S_IXOTH),
)


def _is_hidden(path: Path) -> bool:
    """Return True when ``path.name`` is a dotfile."""
    return path.name.startswith(".")


def _permission_string(mode: int) -> str:
    """Return the ``rwxr-xr-x`` style permission string for a ``st_mode``."""
    return "".join(
        char if mode & bit else "-"
        for trio in _PERMISSION_TRIOS
        for bit, char in zip(trio, _PERMISSION_CHARS, strict=True)
    )


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


class FileManager:
    """Read-only filesystem access used by the UI modules."""

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
        return _permission_string(Path(path).lstat().st_mode)

    def parent_of(self, path: str | os.PathLike[str]) -> Path:
        """Return the parent directory of ``path`` (path maths only, no disk read)."""
        target = Path(path)
        return target.parent if target.parent != target else target

    def can_ascend(self, path: str | os.PathLike[str]) -> bool:
        """True when ``path`` has a distinct parent directory to navigate to."""
        target = Path(path)
        return target.parent != target

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
            permissions=_permission_string(info.st_mode),
            kind=_kind_of(child.name, is_dir, is_symlink),
            suffix=_suffix_of(child.name),
        )

    @staticmethod
    def _sort_key(entry: FileEntry) -> tuple[bool, str]:
        """Directories first, then case-insensitive by name."""
        return (not entry.is_dir, entry.name.casefold())
