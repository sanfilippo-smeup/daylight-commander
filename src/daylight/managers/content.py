"""Reading a file for the internal viewer: type, MIME, checksum, formatting.

Every byte this application reads on behalf of the viewer is read here (AC2-AC4,
FR-301..FR-305), which is what keeps the rule that the UI never touches the disk
true even for a viewer that has to *stream* a file.

Two things are deliberately bounded:

* :data:`PRETTY_LIMIT` is the largest document the pretty formatters will read
  whole. A ``.json``/``.md`` file above it is reported truncated and the viewer
  falls back to a windowed raw view.
* the plain-text path never materialises the file at all: :func:`read_window`
  seeks a line range and :func:`count_lines` streams the file counting newlines,
  so a 2 GB log costs the event loop one screenful of lines.
"""

from __future__ import annotations

import codecs
import grp
import hashlib
import itertools
import json
import mimetypes
import os
import pwd
import stat as stat_module
from pathlib import Path

from daylight.models import FileProperties, ViewDocument

__all__ = [
    "CHECKSUM_ALGORITHM",
    "CHECKSUM_CHUNK",
    "LINE_COUNT_LIMIT",
    "PRETTY_LIMIT",
    "SNIFF_BYTES",
    "checksum",
    "count_lines",
    "describe",
    "find_matches",
    "is_text_bytes",
    "is_text_file",
    "load_view",
    "mime_type",
    "permission_string",
    "read_text",
    "read_window",
    "view_kind",
]

#: Bytes read to decide text vs binary. The decision only needs the head of the
#: file, so opening a 4 GB movie costs 8 KiB.
SNIFF_BYTES = 8192

#: Largest document the JSON/Markdown formatters read whole (1 MiB).
PRETTY_LIMIT = 1_048_576

#: Largest file whose line count is pre-computed (64 MiB).
#:
#: Above it the viewer reports "?" instead of scanning the file just to draw a
#: position indicator, and lets the window run into the end of the file.
LINE_COUNT_LIMIT = 67_108_864

#: Bytes per checksum round.
CHECKSUM_CHUNK = 1_048_576

#: Default checksum shown in the properties view.
CHECKSUM_ALGORITHM = "sha256"

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdown", ".mkd"})
JSON_SUFFIXES = frozenset({".json", ".jsonc"})

#: Byte order marks, longest first: the UTF-32 mark starts like the UTF-16 one.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _target(path: str | os.PathLike[str]) -> Path:
    """Return ``path`` as a :class:`~pathlib.Path`."""
    return Path(path)


# -- text or binary ---------------------------------------------------------


def is_text_bytes(data: bytes) -> bool:
    """Return True when ``data`` looks like text rather than binary.

    A NUL byte is the classic giveaway of a binary blob, and anything that does
    not decode as UTF-8 is treated as binary too. UTF-16/UTF-32 marks are text by
    definition, so they are honoured before the decode is attempted.
    """
    if any(data.startswith(mark) for mark, _ in _BOMS):
        return True
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def is_text_file(path: str | os.PathLike[str], *, sniff: int = SNIFF_BYTES) -> bool:
    """Return True when the head of ``path`` decodes as text (FR-305)."""
    try:
        with _target(path).open("rb") as handle:
            head = handle.read(max(1, sniff))
    except OSError:
        return False
    return is_text_bytes(head)


def _encoding_for(path: Path) -> str:
    """Return the text encoding implied by ``path``'s byte order mark."""
    try:
        with path.open("rb") as handle:
            head = handle.read(4)
    except OSError:
        return "utf-8"
    for mark, encoding in _BOMS:
        if head.startswith(mark):
            return encoding
    return "utf-8"


# -- type and MIME ----------------------------------------------------------


def view_kind(path: str | os.PathLike[str]) -> str:
    """Return the view kind for ``path``: markdown/json/text/binary/directory.

    A ``.json`` that turns out to hold binary bytes is reported as ``binary``,
    because the suffix never overrides what the bytes say (AC4). YAML is not a
    kind of its own: it is plain text, shown verbatim with line numbers, because
    re-indenting it could change what the document *means*.
    """
    target = _target(path)
    if target.is_dir():
        return "directory"
    if not is_text_file(target):
        return "binary"
    suffix = target.suffix.casefold()
    if suffix in MARKDOWN_SUFFIXES:
        return "markdown"
    if suffix in JSON_SUFFIXES:
        return "json"
    return "text"


def mime_type(path: str | os.PathLike[str]) -> str:
    """Return the MIME type of ``path`` (FR-303).

    A directory reports ``inode/directory``; a suffix the stdlib does not know
    falls back to the bytes: text, then the generic binary type.
    """
    target = _target(path)
    if target.is_dir():
        return "inode/directory"
    guessed = mimetypes.guess_type(target.name)[0]
    if guessed:
        return guessed
    return "text/plain" if is_text_file(target) else "application/octet-stream"


_PERMISSION_CHARS = ("r", "w", "x")

#: The user/group/other read-write-execute bit triples, in display order.
_PERMISSION_TRIOS = (
    (stat_module.S_IRUSR, stat_module.S_IWUSR, stat_module.S_IXUSR),
    (stat_module.S_IRGRP, stat_module.S_IWGRP, stat_module.S_IXGRP),
    (stat_module.S_IROTH, stat_module.S_IWOTH, stat_module.S_IXOTH),
)


def permission_string(mode: int) -> str:
    """Return the ``rwxr-xr-x`` style permission string for a ``st_mode``.

    The single source of truth for both listings (:class:`FileManager`) and the
    properties view (:func:`describe`).
    """
    return "".join(
        char if mode & bit else "-"
        for trio in _PERMISSION_TRIOS
        for bit, char in zip(trio, _PERMISSION_CHARS, strict=True)
    )


# -- checksums --------------------------------------------------------------


def checksum(
    path: str | os.PathLike[str],
    algorithm: str = CHECKSUM_ALGORITHM,
    *,
    chunk: int = CHECKSUM_CHUNK,
) -> str:
    """Return the hex checksum of ``path``, read in ``chunk`` sized pieces.

    Streaming rather than ``read_bytes``: hashing a 2 GB file must not need
    2 GB of memory.
    """
    digest = hashlib.new(algorithm)
    with _target(path).open("rb") as handle:
        for block in iter(lambda: handle.read(max(1, chunk)), b""):
            digest.update(block)
    return digest.hexdigest()


# -- reading (all of it bounded) --------------------------------------------


def read_text(path: str | os.PathLike[str], *, limit: int = PRETTY_LIMIT) -> tuple[str, bool]:
    """Return ``(text, truncated)`` for the first ``limit`` characters of ``path``.

    At most ``limit + 1`` characters are read, so asking for the head of a huge
    file does not read the whole file. A truncated read is cut back to the last
    complete line, because half a line is not worth showing.
    """
    target = _target(path)
    with target.open("r", encoding=_encoding_for(target), errors="replace") as handle:
        text = handle.read(limit + 1)
    if len(text) <= limit:
        return text, False
    cut = text.rfind("\n", 0, limit)
    return (text[: cut + 1] if cut > 0 else text[:limit]), True


def read_window(
    path: str | os.PathLike[str],
    start: int,
    count: int,
) -> list[str]:
    """Return up to ``count`` lines of ``path`` starting at line ``start``.

    Lazy: the file is iterated and the requested slice is the only thing kept, so
    the cost of a window is independent of how far into the file it starts. The
    line terminators are stripped, so a line is a line however the file ends it.
    """
    target = _target(path)
    first = max(0, start)
    stop = first + max(0, count)
    with target.open("r", encoding=_encoding_for(target), errors="replace") as handle:
        return [line.rstrip("\n") for line in itertools.islice(handle, first, stop)]


def count_lines(path: str | os.PathLike[str], *, limit: int | None = None) -> int | None:
    """Return how many lines ``path`` has, or None when it is too big to scan.

    The count is streamed (one block at a time, never held) and skipped entirely
    above ``limit`` bytes (:data:`LINE_COUNT_LIMIT` by default), where it would
    cost more than the position indicator is worth.
    """
    ceiling = LINE_COUNT_LIMIT if limit is None else limit
    target = _target(path)
    try:
        if target.stat().st_size > ceiling:
            return None
    except OSError:
        return None
    total = 0
    with target.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            total += block.count(b"\n")
    if total and not _ends_with_newline(target):
        total += 1
    return total


def _ends_with_newline(path: Path) -> bool:
    """Return True when the last byte of ``path`` is a newline."""
    try:
        with path.open("rb") as handle:
            try:
                handle.seek(-1, os.SEEK_END)
            except OSError:
                return True
            return handle.read(1) == b"\n"
    except OSError:
        return True


def find_matches(
    path: str | os.PathLike[str],
    needle: str,
    *,
    max_hits: int = 200,
) -> list[int]:
    """Return the 1-based line numbers of ``path`` that contain ``needle``.

    Streams the file and stops at ``max_hits``, so a search in a large log never
    materialises it and a hopeless needle cannot run forever.
    """
    text = needle.casefold()
    if not text:
        return []
    hits: list[int] = []
    with _target(path).open(
        "r", encoding=_encoding_for(_target(path)), errors="replace"
    ) as handle:
        for number, line in enumerate(handle, start=1):
            if text in line.casefold():
                hits.append(number)
                if len(hits) >= max_hits:
                    break
    return hits


# -- formatting -------------------------------------------------------------


def _format_json(text: str) -> tuple[str, str]:
    """Return ``(pretty_text, notice)`` for a JSON document."""
    try:
        loaded = json.loads(text)
    except (ValueError, RecursionError) as error:
        return text, f"not valid JSON ({error.__class__.__name__}); shown as text"
    return json.dumps(loaded, indent=2, ensure_ascii=False, sort_keys=False), ""


def load_view(
    path: str | os.PathLike[str],
    *,
    limit: int = PRETTY_LIMIT,
) -> ViewDocument:
    """Return what the internal viewer should draw for ``path`` (AC2, AC3).

    ``markdown``/``json`` come back formatted; ``text`` -- YAML included -- comes
    back with an empty body because that viewer asks the manager for one window of
    lines at a time; ``binary`` comes back with no body at all, since a binary
    file is never rendered inline (AC4).
    """
    target = _target(path)
    try:
        size = target.stat().st_size
    except OSError:
        size = 0
    kind = view_kind(target)
    if kind in ("binary", "directory"):
        return ViewDocument(kind=kind, size=size)
    if kind == "text":
        # The windowed viewer never needs the whole file, so hand it no body at
        # all: it asks for one window of lines at a time instead.
        return ViewDocument(kind=kind, size=size)
    text, truncated = read_text(target, limit=limit)
    notice = ""
    if kind == "json" and not truncated:
        text, notice = _format_json(text)
    elif truncated:
        notice = (
            f"file is larger than {limit // 1024} KiB: "
            f"showing the first {limit // 1024} KiB"
        )
    return ViewDocument(kind=kind, text=text, notice=notice, truncated=truncated, size=size)


# -- properties -------------------------------------------------------------


def _owner_of(uid: int) -> str:
    """Return the user name for ``uid``, or the number when it is unknown."""
    try:
        return pwd.getpwuid(uid).pw_name
    except (KeyError, OSError, ImportError):
        return str(uid)


def _group_of(gid: int) -> str:
    """Return the group name for ``gid``, or the number when it is unknown."""
    try:
        return grp.getgrgid(gid).gr_name
    except (KeyError, OSError, ImportError):
        return str(gid)


def describe(
    path: str | os.PathLike[str],
    *,
    algorithm: str = CHECKSUM_ALGORITHM,
    with_checksum: bool = True,
) -> FileProperties:
    """Return the :class:`FileProperties` the properties view lists (FR-303).

    ``lstat`` is used, so a symlink is described as a symlink rather than as its
    target. A directory has no checksum (there is nothing to hash) and a missing
    file raises ``FileNotFoundError``, so a deleted-between-listing-and-view file
    is reported rather than shown as empty.
    """
    target = _target(path)
    info = target.lstat()
    kind = view_kind(target)
    is_dir = stat_module.S_ISDIR(info.st_mode)
    return FileProperties(
        name=target.name or str(target),
        path=target,
        is_dir=is_dir,
        is_symlink=stat_module.S_ISLNK(info.st_mode),
        size=info.st_size,
        inode=info.st_ino,
        links=info.st_nlink,
        permissions=permission_string(info.st_mode),
        octal_permissions=oct(stat_module.S_IMODE(info.st_mode))[2:].zfill(4),
        owner=_owner_of(info.st_uid),
        group=_group_of(info.st_gid),
        mime=mime_type(target),
        is_text=kind not in ("binary", "directory"),
        view_kind=kind,
        checksum_algorithm=algorithm,
        checksum=checksum(target, algorithm) if with_checksum and not is_dir else "",
        modified=info.st_mtime,
        changed=info.st_ctime,
        accessed=info.st_atime,
        created=getattr(info, "st_birthtime", None),
    )
