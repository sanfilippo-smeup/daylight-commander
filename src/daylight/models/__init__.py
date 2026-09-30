"""Core data models: no UI and no filesystem access lives here."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

__all__ = [
    "AppConfig",
    "DirEntry",
    "FileEntry",
    "FileProperties",
    "Theme",
    "ViewDocument",
    "VIEW_KINDS",
]

#: Name of the synthetic row that navigates to the parent directory.
PARENT_NAME = ".."

#: ``DirEntry.kind`` values, ordered from most to least "container like".
ENTRY_KINDS: tuple[str, ...] = ("parent", "dir", "symlink", "file")

#: ``FileProperties.view_kind`` / ``ViewDocument.kind`` values (DC-004).
#:
#: ``binary`` is never rendered inline (FR-305): it is handed to the OS default
#: viewer instead, and ``directory`` is not a document at all. YAML has no kind of
#: its own: it is ``text``, shown verbatim, because re-indenting it can change
#: what the document means.
VIEW_KINDS: tuple[str, ...] = ("markdown", "json", "text", "binary", "directory")


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
class ViewDocument:
    """What the internal viewer should draw for one file (DC-004).

    Produced only by :mod:`daylight.managers.content`, so the viewer widget never
    decides *how* a format is read: it only renders :attr:`kind` and shows
    :attr:`notice` when the content had to be degraded.

For ``markdown``/``json`` :attr:`text` is the formatted document and
    is bounded by ``PRETTY_LIMIT``; :attr:`truncated` then says so. For ``text``
    (YAML included) the widget asks the manager for a window of lines instead,
    because a source file must never be materialised whole.
    """

    kind: str
    text: str = ""
    notice: str = ""
    truncated: bool = False
    size: int = 0

    @property
    def is_text_kind(self) -> bool:
        """True for the windowed plain/source viewer."""
        return self.kind == "text"


@dataclass(frozen=True)
class FileProperties:
    """Everything the properties view shows about one entry (FR-303).

    Built by :meth:`daylight.managers.FileManager.describe`, so the widget that
    lists the fields never reads ``stat``, the owner database or the file bytes.
    """

    name: str
    path: Path
    is_dir: bool
    is_symlink: bool
    size: int
    inode: int
    links: int
    permissions: str
    octal_permissions: str
    owner: str
    group: str
    mime: str
    is_text: bool
    view_kind: str
    checksum_algorithm: str
    checksum: str
    modified: float
    changed: float
    accessed: float
    created: float | None = None


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
