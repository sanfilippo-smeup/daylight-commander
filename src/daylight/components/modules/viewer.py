"""The internal viewer: Markdown, JSON and plain text, in one widget.

The viewer is mounted into the *inactive* panel's mount point by the Files page
(ADR-010) and is never pushed as a screen, so it can never take the whole
terminal -- and it is deliberately not focusable: the keyboard stays in the
listing, and the page's viewer keys (``ctrl+up``/``ctrl+down``/``ctrl+f``/``ctrl+g``)
drive this widget from there. That is also why reading a file cannot make a stray
``f8`` delete it.

Everything the viewer shows is decided by :mod:`daylight.managers.content` and
fetched through :class:`daylight.managers.FileManager`, so no disk access happens
here. Plain text is windowed: only the lines on screen are ever held, which is
what makes a multi-gigabyte log behave like a 40-line file.

The rendering is deliberately plain -- line numbers and matched-line
highlighting, no token colouring -- because the stack is fixed at Textual and a
syntax highlighter would be a new dependency. What *is* format aware is how a
file is read: Markdown goes through Textual's ``Markdown`` widget, JSON is pretty
printed, and everything else -- YAML and other source text -- is a numbered,
searchable text window whose size policy follows the detected kind. Nothing the
viewer shows is ever written back: the bytes on disk are the bytes on screen.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Resize
from textual.widgets import Input, Markdown, Static

from daylight.managers import FileManager
from daylight.models import ViewDocument

__all__ = ["ViewerPanel", "render_lines"]

#: Fallback window size before the layout has told the viewer how tall it is.
DEFAULT_WINDOW_ROWS = 24

#: Stand-in for "past the end" on a file whose length was never counted.
_UNCOUNTED_END = 1 << 40

#: Style applied to the matched text of the current search.
MATCH_STYLE = "reverse"

#: The keys that drive the viewer, shown in the header so they are discoverable.
KEY_HINTS = (
    "F3 view \u2022 Alt+Enter props \u2022 F4 edit \u2022 Ctrl+F find \u2022 Ctrl+G next"
    " \u2022 esc close"
)


def render_lines(lines: list[str], start: int, *, needle: str = "", width: int = 5) -> Text:
    """Return the numbered block for one window of ``lines``.

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
        self.line_start = 0
        self.line_count: int | None = None
        self.hits: list[int] = []
        self.hit_index = -1
        self.needle = ""
        self._loading = False
        self._window_rows = 0
        self._pending_scroll = 0
        # The whole formatted document, for the kinds that are read whole and
        # scrolled in memory (json). None means "not held": plain text -- YAML
        # included -- is windowed instead, and Markdown scrolls its own rendered
        # body.
        self._document_lines: list[str] | None = None
        self._markdown: Markdown | None = None
        self._header = Static("", classes="viewer-header")
        self._notice = Static("", classes="viewer-notice")
        self._body = VerticalScroll(classes="viewer-body")
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
    def is_windowed(self) -> bool:
        """True for the plain-text viewer, the one that reads a file line by line."""
        return self.document.is_text_kind

    @property
    def is_rendered(self) -> bool:
        """True when the body is Textual's Markdown widget rather than numbered text."""
        return self._markdown is not None

    @property
    def loading(self) -> bool:
        """True while a window or a document is being read off the event loop."""
        return self._loading

    @property
    def body(self) -> VerticalScroll:
        """The scrollable area the rendered content sits in."""
        return self._body

    @property
    def text_widget(self) -> Static:
        """The widget the numbered text window is rendered into."""
        return self._text

    @property
    def markdown_widget(self) -> Markdown | None:
        """The Markdown widget, when the file is being rendered as Markdown."""
        return self._markdown

    @property
    def search_input(self) -> Input:
        """The search box, hidden until ``ctrl+f`` asks for it."""
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

    def on_resize(self, event: Resize) -> None:
        """Re-read the window only when a resize changed how many lines fit."""
        del event
        if self.is_windowed and not self._loading and self._window_rows != self.window_rows:
            self.read_window(self.line_start)

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
        """Read the document (or the first window of it) in a worker thread."""
        self._loading = True
        self.read_document()

    @work(thread=True, exclusive=True, group="viewer-load")
    def read_document(self) -> None:
        """Ask the manager for the document and open the first window."""
        manager = self.manager
        document = manager.load_view(self.path)
        total: int | None = None
        lines: list[str] = []
        if document.is_text_kind:
            total = manager.count_lines(self.path)
            lines = manager.read_window(self.path, 0, self.window_rows)
        self.app.call_from_thread(self.show_document, document, total, lines)

    def show_document(self, document: ViewDocument, total: int | None, lines: list[str]) -> None:
        """Install a freshly read document and draw it.

        Markdown is handed to Textual's ``Markdown`` widget, which is what renders
        headings, lists, code blocks, tables and links (AC2). JSON is shown as the
        *formatted* text it became -- verbatim and numbered, not run back through
        Markdown, which would reflow the very layout that makes it readable.
        """
        self.document = document
        self.lines = lines
        self.line_start = 0
        self._loading = False
        self.hits = []
        self.hit_index = -1
        self.needle = ""
        self._search.value = ""
        self._notice.update(document.notice)
        self._notice.display = bool(document.notice)
        # The rendered body is reused, never stacked: a reload (an edit, a second
        # open of the same file) re-renders into the widget already on screen.
        markdown, self._markdown = self._markdown, None
        if document.kind == "markdown":
            self._document_lines = None
            self.line_count = None
            self._markdown = self._draw_markdown(document.text, markdown)
        elif document.is_text_kind:
            self._document_lines = None
            self.line_count = total
            self._window_rows = len(lines)
            self._draw_text()
        else:
            self._document_lines = document.text.splitlines()
            self.line_count = len(self._document_lines)
            self._window_rows = self.window_rows
            self.lines = self._document_lines[: self.window_rows]
            self._draw_text()
        self.update_header()
        self.update_status()
        self._flush_pending_scroll()

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

    # -- the text window ---------------------------------------------------

    @property
    def window_rows(self) -> int:
        """How many lines fit in the body, and so how many the window holds."""
        return max(1, self._body.size.height or DEFAULT_WINDOW_ROWS)

    @property
    def gutter_width(self) -> int:
        """Digits needed by the largest line number the window can reach."""
        highest = self.line_start + self.window_rows
        if self.line_count is not None:
            highest = min(highest, self.line_count)
        return max(3, len(str(max(1, highest))))

    @property
    def last_line(self) -> int:
        """The last 0-based line the window may start on, given the file length.

        A file too big to count has no known end, so the window is free to walk
        it: reading past the end simply comes back empty, which
        :meth:`show_window` turns into "the last full window".
        """
        if self.line_count is None:
            return _UNCOUNTED_END
        return max(0, self.line_count - self.window_rows)

    def scroll_lines(self, delta: int) -> None:
        """Move the view by ``delta`` lines, clamped to the content.

        Keys pressed while a window is still being read are remembered instead of
        dropped, so holding a key down keeps moving.
        """
        if self._markdown is not None:
            return
        if self._loading:
            self._pending_scroll += delta
            return
        start = self.line_start + delta
        if self._document_lines is not None:
            self.show_window(*self._memory_window(start))
        else:
            self.read_window(start)

    def _flush_pending_scroll(self) -> None:
        """Apply the scrolls that arrived while a window was being read."""
        if not self._pending_scroll:
            return
        pending, self._pending_scroll = self._pending_scroll, 0
        self.scroll_lines(pending)

    def scroll_pages(self, delta: int) -> None:
        """Move the view by ``delta`` screenfuls."""
        self.scroll_lines(delta * self.window_rows)

    def scroll_to_line(self, line: int) -> None:
        """Put the top of the view on 1-based ``line``."""
        if self._markdown is not None:
            return
        start = max(0, line - 1)
        if self._document_lines is not None:
            self.show_window(*self._memory_window(start))
        else:
            self.read_window(start)

    def read_window(self, start: int) -> None:
        """Clamp ``start`` to the file and read that window in a worker thread."""
        offset = max(0, min(start, self.last_line))
        rows = self.window_rows
        if offset == self.line_start and self.lines and rows == self._window_rows:
            return
        self._loading = True
        self.load_window(offset, rows)

    @work(thread=True, exclusive=True, group="viewer-window")
    def load_window(self, start: int, rows: int) -> None:
        """Read one window of lines off the event loop."""
        lines = self.manager.read_window(self.path, start, rows)
        self.app.call_from_thread(self.show_window, start, rows, lines)

    def _memory_window(self, start: int) -> tuple[int, int, list[str]]:
        """Return the ``(start, rows, lines)`` window of an in-memory document."""
        held = self._document_lines or []
        rows = self.window_rows
        offset = max(0, min(start, max(0, len(held) - 1)))
        return offset, rows, held[offset : offset + rows]

    def show_window(self, start: int, rows: int, lines: list[str]) -> None:
        """Draw a window of lines, whether read from the file or from memory."""
        if not lines and self.line_count is None and start > 0:
            # A file too big to count has no known end, so an empty window *is*
            # the end of it: step back one window instead of showing nothing.
            self.read_window(max(0, start - rows))
            return
        self.line_start = start
        self.lines = lines
        self._window_rows = rows
        self._loading = False
        self._draw_text()
        self.update_status()
        self._flush_pending_scroll()

    def _draw_text(self) -> None:
        """Render the current lines into the numbered text widget."""
        self._text.update(
            render_lines(
                self.lines,
                self.line_start,
                needle=self.needle,
                width=self.gutter_width,
            )
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
        if self._markdown is None:
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
            if self._markdown is None:
                self._draw_text()
            self.update_status()
            return
        if self._document_lines is not None:
            self.show_hits(self._matches(self._document_lines))
        elif self._markdown is not None:
            self.show_hits(self._matches(self.document.text.splitlines()))
        else:
            self.find_in_file()  # the file is not held: scan it in a worker

    def _matches(self, lines: list[str]) -> list[int]:
        """Return the 1-based line numbers of ``lines`` holding the needle."""
        lowered = self.needle.casefold()
        return [
            number for number, line in enumerate(lines, start=1) if lowered in line.casefold()
        ]

    @work(thread=True, exclusive=True, group="viewer-search")
    def find_in_file(self) -> None:
        """Scan the file for the needle in a worker thread, never in memory."""
        hits = self.manager.find_matches(self.path, self.needle)
        self.app.call_from_thread(self.show_hits, hits)

    def show_hits(self, hits: list[int]) -> None:
        """Install the match list found by :meth:`find_in_file`."""
        self.hits = hits
        self.jump_to_hit()

    def jump_to_hit(self) -> None:
        """Scroll to the current match."""
        if not self.hits:
            self.update_status()
            return
        self.hit_index = max(0, self.hit_index) % len(self.hits)
        line = self.hits[self.hit_index]
        if self._document_lines is not None:
            self.show_window(*self._memory_window(line - 1))
        elif self._markdown is not None:
            self._body.scroll_to(y=max(0, line - 1), animate=False)
        else:
            self.read_window(line - 1)
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
        elif self._markdown is not None:
            text = f" {self.kind} \u2502  rendered"
        elif self.line_count is None:
            position = f"line {self.line_start + 1}+ (large file, not counted)"
            text = f" {position} \u2502  {len(self.lines)} lines loaded"
        else:
            text = f" line {self.line_start + 1} of {self.line_count}"
            text = f"{text} \u2502  {len(self.lines)} lines shown"
        self._status.update(text)
