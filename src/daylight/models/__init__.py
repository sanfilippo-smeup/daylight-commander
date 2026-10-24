"""Core data models: no UI and no filesystem access lives here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Mapping

__all__ = [
    "AppConfig",
    "BuilderEvent",
    "BuilderOption",
    "BuilderQuestion",
    "DashboardArtifact",
    "DashboardBlock",
    "DashboardBlockData",
    "DashboardSpec",
    "DashboardSummaryValue",
    "DashboardTable",
    "DirEntry",
    "DirectoryStats",
    "FileEntry",
    "FileProperties",
    "FileStat",
    "SandboxResult",
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
    #: Whether this palette is a dark one. Textual derives different widget
    #: variables for light and dark (scrollbars, inputs, the block cursor), so a
    #: light theme has to say so instead of shipping light colors on a dark
    #: design system.
    dark: bool = True


@dataclass(frozen=True)
class AppConfig:
    """User-visible application settings."""

    theme: str = "daylight-dark"
    start_path: Path | None = None
    show_hidden: bool = False
    sort_key: str = "name"
    sort_reverse: bool = False
    keybindings: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FileStat:
    """One file in a statistics insight list (top-N, oldest/newest) (DC-023).

    Produced only by :mod:`daylight.managers.stats`, so the chart code never
    reads ``stat`` itself.
    """

    path: Path
    name: str
    size: int
    modified: float


@dataclass(frozen=True)
class DirectoryStats:
    """Recursive statistics of one directory tree (DC-023, ADR-013).

    Built only by :func:`daylight.managers.stats.build_stats`; the Stats page
    renders it and never touches the disk for statistics. The mapping and tuple
    fields are the chart inputs. ``partial`` is True when the entry cap or the
    time budget stopped the walk early, so every card built from it can say so.

    Counts are of the tree *below* ``root``: the root itself is not an entry,
    symlinks are counted but never followed or sized, and only regular files
    contribute size, timeline and insight data.
    """

    root: Path
    total_size: int
    file_count: int
    directory_count: int
    hidden_count: int
    symlink_count: int
    scanned_entries: int
    partial: bool
    elapsed: float
    category_sizes: Mapping[str, int]
    extension_sizes: Mapping[str, int]
    extension_counts: Mapping[str, int]
    size_buckets: Mapping[str, int]
    permission_counts: Mapping[str, int]
    daily_counts: Mapping[date, int]
    monthly_counts: Mapping[str, int]
    largest: tuple[FileStat, ...]
    oldest: FileStat | None
    newest: FileStat | None

    @property
    def dirs_files_ratio(self) -> str:
        """The ``dirs:files`` ratio the summary card shows, e.g. ``2:5``."""
        return f"{self.directory_count}:{self.file_count}"


@dataclass(frozen=True)
class BuilderOption:
    """One selectable choice of an AI builder clarifying question (DC-024).

    Mirrors the opencode ``QuestionOption`` shape: what the user sees
    (:attr:`label`) and, optionally, why they would pick it
    (:attr:`description`).
    """

    label: str
    description: str = ""


@dataclass(frozen=True)
class BuilderQuestion:
    """One pending clarifying question asked by the AI builder (DC-024).

    Normalized from an opencode question request (which may bundle several
    questions): :attr:`id` is the request id used to reply, the remaining
    fields describe its first question. ``multiple`` allows several options to
    be selected, ``custom`` allows a free-text answer.
    """

    id: str
    header: str
    question: str
    options: tuple[BuilderOption, ...] = ()
    multiple: bool = False
    custom: bool = False
    #: opencode v2 form routing (ADR-015): the session that owns the form and
    #: the field key the answer must be keyed by. Empty for v1 questions.
    session_id: str = ""
    field_key: str = ""


@dataclass(frozen=True)
class BuilderEvent:
    """One parsed event of an AI builder turn (DC-024).

    :attr:`kind` is ``"text"`` for streamed assistant text (in :attr:`text`),
    ``"question"`` for a pending clarifying question (in :attr:`question`) and
    ``"idle"`` when the session went idle, i.e. the turn is over.
    """

    kind: str
    text: str = ""
    question: BuilderQuestion | None = None


@dataclass(frozen=True)
class SandboxResult:
    """Outcome of one sandboxed script run (DC-024).

    ``exit_code`` is the script's exit status (negative when a signal killed
    it), ``timed_out`` is True when the wall-clock timeout had to kill it, and
    ``duration_seconds`` is the observed wall-clock time.
    """

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float


@dataclass(frozen=True)
class DashboardArtifact:
    """One saved AI dashboard: name, prompt, spec and timestamps (FR-806).

    A Revision-6 artifact carries the validated :attr:`spec` and the
    :attr:`directory` it was built for. A Revision-5 artifact instead carries
    only :attr:`script` and is marked :attr:`legacy`: it stays readable but is
    never rendered or executed (DC-031, ADR-016).
    """

    id: str
    name: str
    prompt: str
    created: float
    modified: float
    spec: DashboardSpec | None = None
    directory: str = ""
    legacy: bool = False
    script: str = ""


@dataclass(frozen=True)
class DashboardBlock:
    """One block of a parsed AI dashboard specification (DC-029, ADR-016).

    Built only by :mod:`daylight.managers.dashboard_spec`, so ``kind`` and
    ``source`` are always an allowlisted pair, ``top_n`` is a true integer
    inside ``1..50`` and ``color_token`` a known theme token. ``title`` may be
    empty: the Dashboard page then shows its own per-kind default.
    """

    kind: str
    source: str
    title: str = ""
    top_n: int = 8
    color_token: str = "accent"


@dataclass(frozen=True)
class DashboardSpec:
    """A parsed AI dashboard specification (DC-029, ADR-016).

    ``schema_version`` is explicit so the contract can evolve without silently
    accepting documents written for another version. Built only by
    :func:`daylight.managers.dashboard_spec.parse_spec`.
    """

    schema_version: int
    title: str
    blocks: tuple[DashboardBlock, ...]


@dataclass(frozen=True)
class DashboardSummaryValue:
    """Render data of one summary card: label, displayed value, optional detail."""

    label: str
    value: str
    detail: str = ""


@dataclass(frozen=True)
class DashboardTable:
    """Render data of a table block: a header row and string cell rows."""

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class DashboardBlockData:
    """One bound block: what the Dashboard page renders for it (DC-029).

    The binder fills exactly the member matching the block's source --
    :attr:`summary` for ``summary``, :attr:`rows` for the ranked series sources
    and ``size_buckets``, :attr:`days` for ``daily_counts`` and :attr:`table`
    for the table sources -- and leaves the others empty.
    """

    block: DashboardBlock
    summary: tuple[DashboardSummaryValue, ...] = ()
    rows: tuple[tuple[str, int], ...] = ()
    days: Mapping[date, int] | None = None
    table: DashboardTable | None = None
