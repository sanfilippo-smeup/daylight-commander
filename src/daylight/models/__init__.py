"""Core data models: no UI and no filesystem access lives here."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

__all__ = ["AppConfig", "FileEntry", "Theme"]


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
