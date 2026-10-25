"""The internal viewer: Markdown, JSON and plain text, in one widget.

The viewer is mounted into the *inactive* panel's mount point by the Files page
(ADR-010) and is never pushed as a screen, so it can never take the whole
terminal -- and it is deliberately not focusable: the keyboard stays in the
listing, and the page's viewer keys (``ctrl+up``/``ctrl+down``/``f2``/``ctrl+g``)
drive this widget from there. That is also why reading a file cannot make a stray
``f8`` delete it.

Everything the viewer shows is decided by :mod:`daylight.managers.content` and
fetched through :class:`daylight.managers.FileManager`, so no disk access happens
here.

Every inline kind now behaves the same way: the file is read once, bounded by
:data:`daylight.managers.content.VIEW_LIMIT`, and the whole (numbered) document
is mounted into one ``VerticalScroll``. That is the same body Markdown has
always used, so the wheel, the scrollbar, arrow/page keys, ``ctrl+up``/
``ctrl+down`` and search jumps all move one native scroll position. Nothing is
windowed and nothing is re-read on resize: a resize simply shows more of the
document that is already mounted.

The rendering is deliberately plain -- line numbers and matched-line
highlighting, no token colouring -- because the stack is fixed at Textual and a
syntax highlighter would be a new dependency. What *is* format aware is how a
file is read: Markdown goes through Textual's ``Markdown`` widget, JSON is pretty
printed, and everything else -- YAML and other source text -- is a numbered,
searchable document. Nothing the viewer shows is ever written back: the bytes on
disk are the bytes on screen.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Markdown, Static

from daylight.managers import FileManager
from daylight.models import ViewDocument

__all__ = ["ViewerPanel", "render_lines"]

#: Fallback page size before the layout has told the viewer how tall it is.
DEFAULT_WINDOW_ROWS = 24

#: Style applied to the matched text of the current search.
MATCH_STYLE = "reverse"

#: Above this many lines a search is not repainted with match highlights.
#:
#: The whole document is mounted in one `Static`, so a needle change would force
#: a full repaint of every line. That is cheap for a normal source file and
#: noticeably slow for a megabyte one, where jumping to the hit plus the match
#: count is the useful part anyway. Highlighting still happens below the limit.
HIGHLIGHT_LINES = 2_000

#: The keys that drive the viewer, shown in the header so they are discoverable.
#:
#: Find is ``F2`` since DC-037: ``Ctrl+F`` cycles the Files-page search modes.
KEY_HINTS = (
    "F3 view \u2022 Alt+Enter props \u2022 F4 edit \u2022 F2 find \u2022 Ctrl+G next"
    " \u2022 esc close"
)


def render_lines(lines: list[str], start: int, *, needle: str = "", width: int = 5) -> Text:
    """Return the numbered block for ``lines``.

    ``start`` is the 0-based line number of the first line, so the gutter shows
    real file line numbers rather than window-relative ones. ``needle``, when
    given, is highlighted where it occurs (case-insensitively). Lines are never
    wrapped, because wrapping would break the alignment of the gutter.
    """
    text = Text(no_wrap=True, overflow="ellipsis")
    for offset, line in enumerate(lines):
        text.append(f"{start + offset + 1:>{width}}", style="dim")
        text.append("  ")
        _append_line(text, line, needle)
        if offset < len(lines) - 1:
            text.append("\n")
    return text


def _append_line(text: Text, line: str, needle: str) -> None:
    """Append one line to ``text``, highlighting ``needle`` when there is one."""
    expanded = line.replace("\t", "    ")
    if not needle:
        text.append(expanded)
        return
    haystack = expanded.casefold()
    lowered = needle.casefold()
    cursor = 0
    while True:
        found = haystack.find(lowered, cursor)
        if found < 0:
            text.append(expanded[cursor:])
            return
        if found > cursor:
            text.append(expanded[cursor:found])
        text.append(expanded[found : found + len(lowered)], style=MATCH_STYLE)
        cursor = found + len(lowered)


def _document_lines(text: str) -> list[str]:
    """Split a document into display lines on ``\\n`` only.

    ``str.splitlines`` also breaks on form feeds, ``\\x85`` and ``U+2028``; those
    are characters inside a line for a source file, and splitting on them would
    invent gutter numbers the file does not have.
    """
    if not text:
        return []
    body = text[:-1] if text.endswith("\n") else text
    return body.split("\n")


class _ViewerBody(VerticalScroll):
    """The scroll container every view kind shares (Markdown included).

    Watching the scroll position keeps the status line honest when the document
    is moved by something other than a viewer action -- the wheel, the scrollbar,
    or the body's own keys once it has focus in the full-page Viewers tab.
    """

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """Repaint the status line whenever the body scrolls."""
        super().watch_scroll_y(old_value, new_value)
        parent = self.parent
        if isinstance(parent, ViewerPanel) and parent.is_mounted:
            parent.update_status()


class ViewerPanel(Vertical):
    """The format-aware internal viewer mounted in the inactive panel (AC1-AC4)."""

    DEFAULT_CLASSES: ClassVar[str] = "viewer"

    #: Not focusable on purpose: the keyboard belongs to the listing, and the page
    #: drives the viewer with its own keys.
    can_focus = False

    def __init__(self, path: Path, *, manager: FileManager, kind: str = "") -> None:
        self.path = Path(path)
        self.manager = manager
        self.document: ViewDocument = ViewDocument(kind=kind or "text")
        self.lines: list[str] = []
        self.line_count: int = 0
        self.hits: list[int] = []
        self.hit_index = -1
        self.needle = ""
        #: A 1-based line the document should be centred on once drawn; set by
        #: :meth:`open_at_line` before the document worker has finished (DC-037).
        self._pending_line = 0
        self._loading = False
        self._highlighted = False
        self._markdown: Markdown | None = None
        self._header = Static("", classes="viewer-header")
        self._notice = Static("", classes="viewer-notice")
        self._body = _ViewerBody(classes="viewer-body")
        self._text = Static("", classes="viewer-text")
        self._search = Input(placeholder="find\u2026", classes="viewer-search")
        self._status = Static("", classes="viewer-status")
        super().__init__()

    # -- queries -----------------------------------------------------------

    @property
    def kind(self) -> str:
        """The view kind currently rendered (markdown/json/text)."""
        return self.document.kind

    @property
    def is_rendered(self) -> bool:
        """True when the body is Textual's Markdown widget rather than numbered text."""
        return self._markdown is not None

    @property
    def loading(self) -> bool:
        """True while the document is being read off the event loop."""
        return self._loading

    @property
    def line_start(self) -> int:
        """The 0-based line at the top of the body: its native scroll position."""
        return int(self._body.scroll_y)

    @property
    def window_rows(self) -> int:
        """How many lines fit in the body, and so how far a page key moves."""
        return max(1, self._body.size.height or DEFAULT_WINDOW_ROWS)

    @property
    def gutter_width(self) -> int:
        """Digits needed by the largest line number in the document."""
        return max(3, len(str(max(1, self.line_count))))

    @property
    def body(self) -> VerticalScroll:
        """The scrollable area the rendered content sits in."""
        return self._body

    @property
    def text_widget(self) -> Static:
        """The widget the numbered text is rendered into."""
        return self._text

    @property
    def markdown_widget(self) -> Markdown | None:
        """The Markdown widget, when the file is being rendered as Markdown."""
        return self._markdown

    @property
    def search_input(self) -> Input:
        """The search box, hidden until ``f2`` asks for it."""
        return self._search

    @property
    def header(self) -> Static:
        """The header line: name, kind, size and the keys that drive the viewer."""
        return self._header

    @property
    def notice(self) -> Static:
        """The line that reports a degraded read (truncation, invalid JSON)."""
        return self._notice

    @property
    def status(self) -> Static:
        """The line that reports position and search hits."""
        return self._status

    # -- lifecycle ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Header, notices, the body, the search box and the status line."""
        yield self._header
        yield self._notice
        yield self._body
        yield self._search
        yield self._status

    def on_mount(self) -> None:
        """Hide the search box and read the file off the event loop."""
        self._search.display = False
        self.update_header()
        self.load()

    @on(Input.Changed, ".viewer-search")
    def on_search_changed(self, event: Input.Changed) -> None:
        """Search as the user types."""
        event.stop()
        self.set_needle(event.value)

    @on(Input.Submitted, ".viewer-search")
    def on_search_submitted(self, event: Input.Submitted) -> None:
        """Return: move to the next match."""
        event.stop()
        self.next_hit()

    # -- loading -----------------------------------------------------------

    def load(self) -> None:
        """Read the document in a worker thread."""
        self._loading = True
        self.read_document()

    @work(thread=True, exclusive=True, group="viewer-load")
    def read_document(self) -> None:
        """Ask the manager for the bounded document and draw it whole."""
        document = self.manager.load_view(self.path)
        self.app.call_from_thread(self.show_document, document)

    def show_document(self, document: ViewDocument) -> None:
        """Install a freshly read document and draw it.

        Markdown is handed to Textual's ``Markdown`` widget, which is what renders
        headings, lists, code blocks, tables and links (AC2). JSON and source text
        are shown as numbered text -- verbatim, never run back through Markdown,
        which would reflow the very layout that makes them readable -- and the
        whole bounded document is mounted at once so the body scrolls natively.
        """
        self.document = document
        self._scroll_to_top()
        self.hits = []
        self.hit_index = -1
        self.needle = ""
        self._highlighted = False
        self._search.value = ""
        self._notice.update(document.notice)
        self._notice.display = bool(document.notice)
        # The rendered body is reused, never stacked: a reload (an edit, a second
        # open of the same file) re-renders into the widget already on screen.
        markdown, self._markdown = self._markdown, None
        if document.kind == "markdown":
            self.lines = []
            self.line_count = 0
            self._markdown = self._draw_markdown(document.text, markdown)
        else:
            self.lines = _document_lines(document.text)
            self.line_count = len(self.lines)
            self._draw_text()
        self._loading = False
        self.update_header()
        self.update_status()
        pending = self._pending_line
        self._pending_line = 0
        if pending:
            self.goto_line(pending)

    def _scroll_to_top(self) -> None:
        """Put the body back on the first line, without animation."""
        self._body.scroll_to(y=0, animate=False, immediate=True)

    def _draw_markdown(self, source: str, previous: Markdown | None) -> Markdown:
        """Render ``source`` as Markdown and return the widget that shows it.

        ``previous`` is the body a reload left mounted, and it is updated in place
        rather than replaced: mounting a second ``Markdown`` into the same body
        would stack two rendered documents in one pane, which is what a reload
        after an edit used to leave behind. Only a body that is gone (the viewer
        was closed and re-opened) is mounted afresh.
        """
        if previous is not None and previous.is_mounted:
            previous.update(source)
            return previous
        widget = Markdown(source, classes="viewer-markdown")
        self._body.mount(widget)
        return widget

    # -- the document ------------------------------------------------------

    def scroll_lines(self, delta: int) -> None:
        """Move the body by ``delta`` lines, clamped to the document."""
        self._body.scroll_relative(y=delta, animate=False, immediate=True)
        self.update_status()

    def scroll_pages(self, delta: int) -> None:
        """Move the body by ``delta`` screenfuls."""
        self.scroll_lines(delta * self.window_rows)

    def scroll_to_line(self, line: int) -> None:
        """Put the top of the body on 1-based ``line``."""
        self._body.scroll_to(y=max(0, line - 1), animate=False, immediate=True)
        self.update_status()

    def goto_line(self, line: int) -> None:
        """Scroll the numbered document so 1-based ``line`` is centred (DC-037).

        Pure scroll maths over the document already mounted: no reload, no
        re-read, and find/highlight state untouched -- the same body the wheel
        and the page keys move. A rendered Markdown body has no line
        addresses, so a jump there is a no-op, and a line the document shows
        beyond (a longer file than the viewer's line cap) clamps to the end.
        """
        if self._markdown is not None or not self.lines:
            return
        target = max(1, min(line, self.line_count))
        top = max(0, target - 1 - max(0, self.window_rows - 1) // 2)
        self._body.scroll_to(y=top, animate=False, immediate=True)
        self.update_status()

    def open_at_line(self, line: int) -> None:
        """Centre ``line`` as soon as the document is drawn (DC-037).

        Opening from a content hit races the document worker: a line queued
        here is consumed by ``show_document`` when the document lands, and the
        immediate call covers a viewer that already finished loading. Called
        twice on one viewer still centres once -- the same line either way.
        """
        self._pending_line = max(0, line)
        if self.lines and not self._loading:
            self.goto_line(self._pending_line)
            self._pending_line = 0

    def _draw_text(self, *, layout: bool = True) -> None:
        """Render the numbered document into the text widget.

        ``layout=False`` is for a needle change: only the styles move, so the
        document's line count (and therefore its height) is unchanged and a full
        layout pass over a long document can be skipped.
        """
        self._text.update(
            render_lines(
                self.lines,
                0,
                needle=self.needle,
                width=self.gutter_width,
            ),
            layout=layout,
        )
        if not self._text.is_mounted:
            self._body.mount(self._text)

    # -- search ------------------------------------------------------------

    def open_search(self) -> None:
        """Reveal the search box and give it the keyboard."""
        self._search.display = True
        self._search.focus()
        self.update_status()

    def close_search(self) -> None:
        """Hide the search box and drop the highlight."""
        if self._search.has_focus:
            self._search.blur()
        self._search.display = False
        self.needle = ""
        self.hits = []
        self.hit_index = -1
        if self._markdown is None and self._highlighted:
            self._highlighted = False
            self._draw_text()
        self.update_status()

    @property
    def search_open(self) -> bool:
        """True while the find box is on screen."""
        return self._search.display

    def set_needle(self, needle: str) -> None:
        """Search for ``needle`` and jump to the first match."""
        self.needle = needle.strip()
        if not self.needle:
            self.hits = []
            self.hit_index = -1
            if self._markdown is None and self._highlighted:
                self._highlighted = False
                self._draw_text()
            self.update_status()
            return
        if self._markdown is not None:
            self.show_hits(self._matches(_document_lines(self.document.text)))
        else:
            # Repaint the mounted document with the matches highlighted, unless
            # it is long enough that the full repaint costs more than it is worth.
            if len(self.lines) <= HIGHLIGHT_LINES:
                self._highlighted = True
                self._draw_text(layout=False)
            self.show_hits(self._matches(self.lines))

    def _matches(self, lines: list[str]) -> list[int]:
        """Return the 1-based line numbers of ``lines`` holding the needle."""
        lowered = self.needle.casefold()
        return [
            number for number, line in enumerate(lines, start=1) if lowered in line.casefold()
        ]

    def show_hits(self, hits: list[int]) -> None:
        """Install the match list, then jump to the first match."""
        self.hits = hits
        self.jump_to_hit()

    def jump_to_hit(self) -> None:
        """Scroll to the current match."""
        if not self.hits:
            self.update_status()
            return
        self.hit_index = max(0, self.hit_index) % len(self.hits)
        self.scroll_to_line(self.hits[self.hit_index])
        self.update_status()

    def next_hit(self) -> None:
        """Move to the next match."""
        if self.hits:
            self.hit_index = (self.hit_index + 1) % len(self.hits)
            self.jump_to_hit()

    # -- header and status -------------------------------------------------

    def update_header(self) -> None:
        """Refresh the header: name, kind, size and the keys that drive the viewer."""
        size = f"{self.document.size} B" if self.document.size else "?"
        self._header.update(
            f" {self.path.name}  \u2502  {self.kind}  \u2502  {size}  \u2502  {KEY_HINTS}"
        )

    def update_status(self) -> None:
        """Refresh the position and the search-result line."""
        if self.needle:
            found = (
                f"{self.hit_index + 1}/{len(self.hits)} matches" if self.hits else "no match"
            )
            text = f" find: {self.needle!r} \u2014 {found}"
            if self._markdown is None and self.hits and not self._highlighted:
                # The document was too long to repaint with match styling.
                text = f"{text} \u2502  highlights off (long file)"
        elif self._markdown is not None:
            text = f" {self.kind} \u2502  rendered"
        else:
            text = f" line {self.line_start + 1} of {self.line_count}"
        self._status.update(text)
