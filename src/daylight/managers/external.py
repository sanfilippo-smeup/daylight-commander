"""Handing a file off to something else: ``$EDITOR`` or the OS default app (AC5).

FR-304 is read-only by default, so "edit" is a hand-off and never an in-app
buffer. The dispatch is a *pure* function of the path and the environment:
:func:`edit_command` returns the argv to run, and :func:`edit_file` runs it.
That split is what makes the dispatch testable without launching vim -- a test
passes its own ``runner`` and asserts on the command it was handed.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from daylight.managers.content import is_text_file

__all__ = [
    "DEFAULT_EDITOR",
    "EditDispatch",
    "edit_command",
    "edit_dispatch",
    "edit_file",
    "editor_command",
    "open_command",
    "open_external",
]

#: Used when neither ``$VISUAL`` nor ``$EDITOR`` is set (AC5: vim).
DEFAULT_EDITOR = "vim"

#: Signature of the callable that runs a command; injected by the tests.
Runner = Callable[[Sequence[str]], int]

logger = logging.getLogger("daylight.managers.external")


class EditDispatch(NamedTuple):
    """What editing a path would run, and why it chose that (FR-304)."""

    kind: str
    command: tuple[str, ...]
    path: Path

    @property
    def is_editor(self) -> bool:
        """True when the file was handed to ``$EDITOR`` rather than the OS."""
        return self.kind == "editor"

    @property
    def is_external(self) -> bool:
        """True when the file was handed to the OS default application."""
        return self.kind == "external"


def editor_command(editor: str | None = None) -> str:
    """Return the editor to use: ``$VISUAL``, then ``$EDITOR``, then vim.

    The value may carry arguments (``EDITOR="code -w"``), so only the first word
    is the program and the rest is passed through to it.
    """
    value = editor or os.environ.get("VISUAL") or os.environ.get("EDITOR") or DEFAULT_EDITOR
    return value.strip() or DEFAULT_EDITOR


def open_command(path: str | os.PathLike[str]) -> tuple[str, ...]:
    """Return the argv that opens ``path`` in the OS default application."""
    target = str(path)
    if sys.platform == "darwin":
        return ("open", target)
    if sys.platform == "win32":
        return ("cmd", "/c", "start", "", target)
    return ("xdg-open", target)


def _is_text(path: Path) -> bool:
    """Return True when ``path`` is a file whose bytes are text."""
    return path.is_file() and is_text_file(path)


def edit_dispatch(
    path: str | os.PathLike[str],
    *,
    editor: str | None = None,
) -> EditDispatch:
    """Decide how ``path`` would be edited, without running anything (AC5).

    Text-based files go to ``$EDITOR`` (vim by default); everything else -- a
    PDF, an image, anything whose bytes are not text -- goes to the OS default
    application. A directory is never editable.
    """
    target = Path(path)
    if _is_text(target):
        parts = tuple(editor_command(editor).split()) + (str(target),)
        return EditDispatch("editor", parts, target)
    return EditDispatch("external", open_command(target), target)


def edit_command(
    path: str | os.PathLike[str],
    *,
    editor: str | None = None,
) -> tuple[str, ...]:
    """Return the argv that edits ``path`` (text -> ``$EDITOR``, else OS app)."""
    return edit_dispatch(path, editor=editor).command


def run(argv: Sequence[str], runner: Runner | None = None) -> int:
    """Run ``argv`` and return its exit status (a failed command is not an error)."""
    command = list(argv)
    try:
        if runner is not None:
            return int(runner(command))
        return subprocess.call(command)
    except OSError as error:
        logger.warning("cannot run %s: %s", command[0], error)
        return 127


def open_external(
    path: str | os.PathLike[str],
    *,
    runner: Runner | None = None,
) -> int:
    """Open ``path`` in the OS default application; return the exit status (AC4)."""
    return run(open_command(path), runner)


def edit_file(
    path: str | os.PathLike[str],
    *,
    editor: str | None = None,
    runner: Runner | None = None,
) -> int:
    """Edit ``path`` by handing it to ``$EDITOR`` or the OS default app (AC5)."""
    return run(edit_command(path, editor=editor), runner)
