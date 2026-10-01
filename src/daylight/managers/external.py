"""Handing a target off to something else: ``$EDITOR``, the OS app, or the browser.

FR-304 is read-only by default, so "edit" is a hand-off and never an in-app
buffer. The dispatch is a *pure* function of the path and the environment:
:func:`edit_command` returns the argv to run, and :func:`edit_file` runs it.
That split is what makes the dispatch testable without launching vim -- a test
passes its own ``runner`` and asserts on the command it was handed.

DC-006 adds the second external target, a URL (FR-402/FR-403): :func:`open_link`
hands a URL to the platform's default browser, and :func:`detect_url` finds one
in text. Every launch in this module is described before it runs -- a
:class:`LaunchRequest` carries the target, the application it would be handed to
and the exact argv -- because the confirmation the UI shows is built from that
request, and a launch the user has not confirmed must never be reachable from
anywhere else.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from daylight.managers.content import is_text_file, read_window

__all__ = [
    "DEFAULT_EDITOR",
    "URL_SCHEMES",
    "EditDispatch",
    "LaunchRequest",
    "describe_edit",
    "describe_open",
    "describe_link",
    "detect_url",
    "detect_url_in_file",
    "edit_command",
    "edit_dispatch",
    "edit_file",
    "editor_command",
    "is_url",
    "launch_file",
    "open_command",
    "open_external",
    "open_link",
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


class LaunchRequest(NamedTuple):
    """An external launch described before anything runs (FR-403).

    The UI never launches from a raw path: it shows ``target``, ``application``
    and ``command`` and only calls :meth:`run` once the user has confirmed, so
    "what will open and with which app" is answered by the same object that
    performs the launch (AC1).
    """

    kind: str
    target: str
    application: str
    command: tuple[str, ...]

    @property
    def program(self) -> str:
        """The program that would run: ``open``, ``xdg-open`` or ``cmd``."""
        return self.command[0] if self.command else ""

    @property
    def is_link(self) -> bool:
        """True when the target is a URL for the default browser (FR-402)."""
        return self.kind == "link"

    @property
    def is_editor(self) -> bool:
        """True when the launch is ``$EDITOR``, which is not an external hand-off."""
        return self.kind == "editor"

    def run(self, runner: Runner | None = None) -> int:
        """Run the launch; return its exit status."""
        return run(self.command, runner)


#: URL schemes handed to the default browser rather than to a file application.
URL_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto", "ftp"})

#: A URL in running text: scheme, host, then the rest of the line.
_URL_PATTERN = re.compile(r"(?:https?|ftp)://[^\s<>\"'\)]+")

#: A ``mailto:`` URL, which has no ``//``.
_MAILTO_PATTERN = re.compile(r"mailto:[^\s<>\"'\)]+")

#: Sentence punctuation that follows a URL in prose but is not part of it.
_TRAILING = ".,;:!?"


def is_url(text: str) -> bool:
    """True when ``text`` is a URL the default browser can open (FR-402).

    A scheme alone is not a URL: ``https://`` has nothing to open, and handing it
    to the platform launcher would open the browser's home page instead of what
    the user wrote. So the part after the scheme has to hold something.
    """
    candidate = text.strip()
    scheme, separator, _rest = candidate.partition(":")
    if not separator or scheme.casefold() not in URL_SCHEMES:
        return False
    return bool(candidate[len(scheme) + 1 :].strip().strip("/"))


def detect_url(text: str) -> str | None:
    """Return the first URL in ``text``, or None when there is none (FR-402).

    Sentence punctuation that follows a URL in prose is trimmed off, so the link
    that is offered to the browser is the link that was written.
    """
    for match in (*_URL_PATTERN.finditer(text), *_MAILTO_PATTERN.finditer(text)):
        return match.group(0).rstrip(_TRAILING)
    return None


def detect_url_in_file(
    path: str | os.PathLike[str],
    *,
    lines: int = 64,
) -> str | None:
    """Return the first URL in the head of a text file, or None (FR-402).

    Only the first ``lines`` lines are read: a link lives in the head of a file
    (``bookmark.txt``, a shell profile), and a whole-file scan of a log would be
    a slow answer to a question the head already answers.
    """
    target = Path(path)
    if not _is_text(target):
        return None
    try:
        head = read_window(target, 0, max(1, lines))
    except OSError:
        return None
    return detect_url("\n".join(head))


def describe_open(path: str | os.PathLike[str]) -> LaunchRequest:
    """Describe opening ``path`` in the OS default application (FR-403)."""
    target = Path(path)
    command = open_command(target)
    return LaunchRequest("file", str(target), "the default application", command)


def describe_link(url: str) -> LaunchRequest:
    """Describe opening ``url`` in the default browser (FR-402, FR-403)."""
    clean = url.strip()
    if not is_url(clean):
        raise ValueError(f"not a URL: {url!r}")
    return LaunchRequest("link", clean, "your default browser", open_command(clean))


def describe_edit(dispatch: EditDispatch) -> LaunchRequest:
    """Describe what editing ``dispatch`` would hand off, and to which app."""
    if dispatch.is_editor:
        return LaunchRequest(
            "editor", str(dispatch.path), "your $EDITOR", dispatch.command
        )
    return describe_open(dispatch.path)


def launch_file(
    path: str | os.PathLike[str],
    *,
    runner: Runner | None = None,
) -> int:
    """Run the OS default application for ``path``; return its exit status."""
    return describe_open(path).run(runner)


def open_link(
    url: str,
    *,
    runner: Runner | None = None,
) -> int:
    """Open ``url`` in the default browser; return the exit status (FR-402).

    ``ValueError`` for anything that is not a URL: a mistyped or unrecognised
    scheme must fail before the platform launcher is handed the string, rather
    than opening something unexpected.
    """
    return describe_link(url).run(runner)


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
