"""Core data models: no UI and no filesystem access lives here."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

__all__ = ["AppConfig", "DirEntry", "FileEntry", "Theme"]

#: Name of the synthetic row that navigates to the parent directory.
PARENT_NAME = ".."

#: ``DirEntry.kind`` values, ordered from most to least "container like".
ENTRY_KINDS: tuple[str, ...] = ("parent", "dir", "symlink", "file")


@dataclass(frozen=True)
class FileEntry:
    """A single filesystem entry as presented by the UI."""

    name: str
    path: Path
    is_dir: bool
    is_symlink: bool = False
    size: int = 0
    modified: float = 0.0
    is_hidden: bool = False


@dataclass(frozen=True)
class DirEntry:
    """A directory entry with every field the Files page needs to render a row.

    Produced only by :class:`daylight.managers.FileManager` so that the UI never
    touches the disk to obtain size, time, permissions or type information.
    """

    name: str
    path: Path
    is_dir: bool
    is_hidden: bool
    is_symlink: bool
    size: int
    modified: float
    permissions: str
    kind: str
    suffix: str

    @property
    def is_parent(self) -> bool:
        """True for the synthetic ``..`` row."""
        return self.kind == "parent"

    @classmethod
    def parent(cls, path: str | Path) -> "DirEntry":
        """Build the ``..`` row that navigates from ``path`` to its parent."""
        target = Path(path)
        return cls(
            name=PARENT_NAME,
            path=target.parent,
            is_dir=True,
            is_hidden=False,
            is_symlink=False,
            size=0,
            modified=0.0,
            permissions="",
            kind="parent",
            suffix="",
        )


@dataclass(frozen=True)
class Theme:
    """A named palette; converted to Textual colors by :mod:`daylight.themes`."""

    name: str
    background: str
    surface: str
    panel: str
    foreground: str
    muted: str
    accent: str
    secondary: str
    success: str
    warning: str
    error: str


@dataclass(frozen=True)
class AppConfig:
    """User-visible application settings."""

    theme: str = "daylight-dark"
    start_path: Path | None = None
    show_hidden: bool = False
    sort_key: str = "name"
    sort_reverse: bool = False
    keybindings: Mapping[str, str] = field(default_factory=dict)
