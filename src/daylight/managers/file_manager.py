"""Filesystem access for the UI. Must never import Textual or any widget code."""

from __future__ import annotations

import os
from pathlib import Path

from daylight.models import FileEntry

__all__ = ["FileManager"]


def _is_hidden(path: Path) -> bool:
    """Return True when ``path.name`` is a dotfile."""
    return path.name.startswith(".")


class FileManager:
    """Read-only filesystem access used by the UI modules."""

    def __init__(self, show_hidden: bool = False) -> None:
        self.show_hidden = show_hidden

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

    @staticmethod
    def _sort_key(entry: FileEntry) -> tuple[bool, str]:
        """Directories first, then case-insensitive by name."""
        return (not entry.is_dir, entry.name.casefold())
