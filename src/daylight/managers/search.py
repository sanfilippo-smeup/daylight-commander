"""Bounded recursive name and content search under one directory (DC-037).

Pure filesystem scanning, no Textual imports and no UI: the Files page asks
:class:`daylight.managers.FileManager` for these iterators, which keeps ADR-003
true (the UI never touches the disk) while every bound lives next to the scan
that enforces it.

Bounds, and why they exist:

* :data:`SEARCH_RESULT_LIMIT` (2000) caps any scan. Recursing until the tree
  ends could collect tens of thousands of rows into one list; a table of 2000
  rows is big and responsive enough.
* :data:`SEARCH_FILE_LIMIT` (1 MiB) is the largest file a *content* scan reads.
  Same bound the viewer applies to whole documents, so a multi-gigabyte log
  costs one ``lstat`` and nothing else.
* A NUL byte in the first :data:`~daylight.managers.content.SNIFF_BYTES`
  (8 KiB) marks a binary file, which is never read for content search.
* Directory symlinks are yielded if their name matches but never descended
  into: an ancestor symlink is a loop, and the loop would never end.

Ordering is deterministic: the tree is walked depth-first, children casefold-
sorted in every directory, so the same tree always yields the same rows in the
same order. Scans are cancellable: the caller shares a :class:`CancelToken`
and the walk unwinds through :class:`SearchCancelled` as soon as it is flipped.
"""

from __future__ import annotations

import os
import threading
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path

from daylight.managers import content

__all__ = [
    "SEARCH_FILE_LIMIT",
    "SEARCH_MIN_QUERY",
    "SEARCH_RESULT_LIMIT",
    "SNIPPET_LIMIT",
    "CancelToken",
    "SearchCancelled",
    "iter_content_matches",
    "iter_name_matches",
    "sanitize_line",
    "snippet_for",
]

#: Most results one scan reports, whatever the tree holds (DC-037 AC3/AC4).
SEARCH_RESULT_LIMIT = 2000

#: Largest file a content scan reads (1 MiB); bigger files are skipped.
SEARCH_FILE_LIMIT = 1_048_576

#: Longest snippet a content hit shows, control characters excluded.
SNIPPET_LIMIT = 200

#: Recursion needs at least this many characters before a scan is worth it.
SEARCH_MIN_QUERY = 2


class SearchCancelled(RuntimeError):
    """Raised out of a scan when its :class:`CancelToken` was cancelled."""


class CancelToken:
    """A thread-safe stop flag shared between the UI and a scan thread.

    The UI flips it the moment a newer query supersedes the running scan (or
    the search is closed); the scanner checks it between entries and unwinds
    quickly instead of finishing a walk nobody will read. Instances are
    callable with the same ``cancelled()`` protocol the batch file operations
    already use, so a token and a plain callable are interchangeable.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Stop the scan that holds this token; safe to call more than once."""
        self._event.set()

    def __call__(self) -> bool:
        """True once :meth:`cancel` was called (the ``cancelled()`` protocol)."""
        return self._event.is_set()


def _folded(needle: str) -> str:
    """Return the needle used for matching: stripped and casefolded."""
    return needle.strip().casefold()


def _stop(cancel: Callable[[], bool] | None) -> None:
    """Raise :class:`SearchCancelled` when ``cancel`` — a token — says stop."""
    if cancel is not None and cancel():
        raise SearchCancelled("search cancelled")


def _visible(name: str, show_hidden: bool) -> bool:
    """True when ``name`` may take part in the scan under the hidden setting."""
    return show_hidden or not name.startswith(".")


def _iter_tree(
    root: Path,
    *,
    show_hidden: bool = False,
    cancel: CancelToken | None = None,
) -> Iterator[tuple[Path, bool]]:
    """Yield ``(path, is_dir)`` for every entry below ``root``, preorder.

    Directories are walked depth-first with every directory's children in
    casefold name order, so hits always arrive in a shape a human could have
    predicted. Hidden entries (and everything under a hidden directory) are
    skipped unless ``show_hidden`` is set; directory symlinks are yielded but
    never entered, which is what keeps a symlink loop bounded.

    Errors (unreadable directories, vanished entries) skip forward instead of
    aborting the scan: a partial, honest result beats nothing.
    """
    # One frame per open directory: its sorted entries and the index the walk
    # will resume at when the frame is popped back to. Explicit lists rather
    # than iterators, so the walk can also be abandoned mid-scan (cancellation)
    # without resource ceremony -- each level's scandir is closed inside
    # :func:`_scandir_sorted` the moment its entries are snapshotted.
    frames: list[tuple[list[os.DirEntry[str]], int]] = [(_scandir_sorted(root), 0)]
    while frames:
        entries, index = frames[-1]
        descended: Path | None = None
        while index < len(entries):
            child = entries[index]
            index += 1
            _stop(cancel)
            if not _visible(child.name, show_hidden):
                continue
            try:
                is_dir = child.is_dir(follow_symlinks=False)
                is_symlink = child.is_symlink()
            except OSError:
                continue
            path = Path(child.path)
            yield path, is_dir
            if is_dir and not is_symlink:
                descended = path
                break
        if descended is None:
            frames.pop()
        else:
            frames[-1] = (entries, index)
            frames.append((_scandir_sorted(descended), 0))


def _scandir_sorted(directory: Path) -> list[os.DirEntry[str]]:
    """Return ``directory``'s entries in casefold name order, or ``[]``."""
    try:
        with os.scandir(directory) as scan:
            return sorted(scan, key=lambda child: child.name.casefold())
    except OSError:
        return []


def iter_name_matches(
    root: str | os.PathLike[str],
    needle: str,
    *,
    show_hidden: bool = False,
    cancel: CancelToken | None = None,
) -> Iterator[Path]:
    """Yield the real paths of entries below ``root`` whose name matches.

    Matching is case-insensitive substring over the bare name, files and
    directories alike (the root itself is never a match). At most
    :data:`SEARCH_RESULT_LIMIT` paths are yielded; the walk stops there.
    """
    folded = _folded(needle)
    if not folded:
        return
    found = 0
    for path, _is_dir in _iter_tree(Path(root), show_hidden=show_hidden, cancel=cancel):
        if folded in path.name.casefold():
            yield path
            found += 1
            if found >= SEARCH_RESULT_LIMIT:
                return


def iter_content_matches(
    root: str | os.PathLike[str],
    needle: str,
    *,
    show_hidden: bool = False,
    cancel: CancelToken | None = None,
) -> Iterator[tuple[Path, int, str]]:
    """Yield ``(path, line, snippet)`` for every text match below ``root``.

    A file contributes hits only when it is a file (not a symlink -- reading
    through one may leave the tree), small enough (:data:`SEARCH_FILE_LIMIT`)
    and free of a NUL byte in its first
    :data:`~daylight.managers.content.SNIFF_BYTES`, which is the binary tell.
    ``line`` is the 1-based line number and ``snippet`` the sanitized, length-
    capped text around the match (:func:`snippet_for`). The cap bounds the
    whole scan at :data:`SEARCH_RESULT_LIMIT` hits.
    """
    folded = _folded(needle)
    if not folded:
        return
    found = 0
    for path, is_dir in _iter_tree(Path(root), show_hidden=show_hidden, cancel=cancel):
        if is_dir or path.is_symlink():
            continue
        try:
            size = path.lstat().st_size
        except OSError:
            continue
        if size > SEARCH_FILE_LIMIT or size == 0:
            continue
        for line, snippet in _match_lines(path, folded, cancel):
            yield path, line, snippet
            found += 1
            if found >= SEARCH_RESULT_LIMIT:
                return


def _match_lines(
    path: Path,
    folded: str,
    cancel: CancelToken | None,
) -> Iterator[tuple[int, str]]:
    """Yield ``(1-based line, snippet)`` for every line of ``path`` matching."""
    try:
        with path.open("rb") as handle:
            head = handle.read(content.SNIFF_BYTES)
    except OSError:
        return
    if b"\x00" in head:
        return
    encoding = content._encoding_for(path)
    try:
        with path.open("r", encoding=encoding, errors="replace") as text:
            for number, line in enumerate(text, start=1):
                _stop(cancel)
                body = sanitize_line(line)
                if folded in body.casefold():
                    yield number, snippet_for(body, folded)
    except OSError:
        return


def sanitize_line(line: str) -> str:
    """Return ``line`` safe for a table cell: tabs wide, control chars gone.

    A raw line can carry tabs and terminal-directed control characters; both
    break a ``DataTable`` row. Tabs are expanded to four spaces (removing one
    would join two words into one) and every remaining control character
    (Unicode category ``Cc``, newlines included) is dropped. The result is
    exactly the text the snippet shows, so nothing is ever matched that the
    user could not have read on screen.
    """
    expanded = line.replace("\t", "    ")
    return "".join(char for char in expanded if unicodedata.category(char) not in ("Cc", "Cn"))


def snippet_for(text: str, folded: str, *, limit: int = SNIPPET_LIMIT) -> str:
    """Return at most ``limit`` characters of ``text``, centred on the match.

    Short lines are returned whole. A line longer than the limit is cut into a
    window around the (casefolded) match, with a Unicode ellipsis on each cut
    end, so a hit at column 5000 is still a useful, one-line view. When the
    folded needle cannot be re-found in the sanitized text, the window opens
    at the start: the cap still holds, just without centring.
    """
    if len(text) <= limit:
        return text
    window = limit - 2  # room for the ellipsis on both cut ends
    at = text.casefold().find(folded)
    start = 0 if at < 0 else max(0, min(at - window // 2, len(text) - window))
    head = "\u2026" if start > 0 else ""
    tail = "\u2026" if start + window < len(text) else ""
    return f"{head}{text[start : start + window]}{tail}"
