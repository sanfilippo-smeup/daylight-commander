"""Dual-pane Files page: listing, sorting, filtering, navigation and operations.

One :class:`FilePanel` per side. Exactly one panel is active at a time
(:attr:`FilesPage.active_panel`); the active panel is the focused one and is the
only one that reacts to navigation, filter and sort keys. Each panel is a
two-tab tabsheet (ADR-011): Tab 1 is the directory view (always visible) and
Tab 2 is the file viewer, shown only when a text file is opened. The inactive
panel keeps a stable mount point (``.panel-mount``) for the internal viewer,
now inside the Viewer tab (ADR-010).

The listing is virtualised (AC1). The directory is scanned, filtered and sorted
in a worker thread, and the panel only ever hands the DataTable the rows that fit
its viewport (:attr:`FilePanel.viewport_rows`); scrolling moves that window
instead of growing the table. A 100k-entry directory therefore costs the event
loop one viewport of work, not 100k rows.

File operations (AC1-AC5) follow the same split: the panel only decides *what*
to operate on -- the marked entries (:meth:`FilePanel.operation_targets`), or
the row under the cursor when nothing is marked -- and every disk effect is a
:class:`daylight.managers.FileManager` call. Destructive work is gated by a
:class:`~daylight.modals.ConfirmScreen` (AC2) and long work runs inside a
:class:`~daylight.modals.ProgressScreen`, whose worker thread keeps the event
loop turning and offers Cancel (AC3).

Viewing and editing (DC-004, AC1-AC6) keep that split as well, and add the rule
that decides where a viewer goes: :meth:`FilesPage.open_view` mounts it into the
*inactive* panel and nowhere else, so the listing being browsed is never covered
and nothing is ever pushed fullscreen. A binary file is never rendered inline,
and neither it nor a URL is ever launched without asking first (DC-006): every
hand-off to another application goes through :meth:`FilesPage.prompt_launch` and
a :class:`~daylight.modals.LaunchScreen` that names the target and the
application (FR-403). ``f4`` still hands text to ``$EDITOR`` -- that runs in this
terminal, so it is not an external launch.
"""

from __future__ import annotations

from pathlib import Path
from time import monotonic

from textual import on, work
from textual.app import ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.events import Key, MouseScrollDown, MouseScrollUp, Resize
from textual.message import Message
from textual.reactive import var
from textual.widget import Widget
from textual.widgets import DataTable, Input, Static, TabbedContent, TabPane, Tabs

from daylight.components import file_table
from daylight.components.modules.properties_view import PropertiesView
from daylight.components.modules.viewer import ViewerPanel
from daylight.forms import LINK_FORM, NEW_FILE_FORM, NEW_FOLDER_FORM, RENAME_FORM
from daylight.managers import ConfigManager, FileManager
from daylight.managers.external import EditDispatch, LaunchRequest, is_url
from daylight.models import DirEntry
from daylight.modals import ConfirmScreen, FormScreen, LaunchScreen, ProgressScreen
from daylight.modals.progress import Operation

__all__ = [
    "PANEL_NAMES",
    "SCROLL_STEP",
    "SCROLLBAR_RESERVE",
    "TYPEAHEAD_TIMEOUT",
    "FilePanel",
    "FilesPage",
    "LocationChanged",
    "TypeAhead",
]

#: The two panes, in display order. ``active_panel`` is one of these names.
PANEL_NAMES: tuple[str, str] = ("left", "right")

#: Rows one mouse-wheel notch moves the virtual viewport.
SCROLL_STEP = 3

#: Rows left empty at the bottom of the table, on purpose.
#:
#: The Name column is auto-width, so a long file name can make the table grow a
#: horizontal scrollbar, and that scrollbar costs a row of viewport. Whether it
#: appears is only known once the rows are laid out, which is too late to size
#: the window from the current geometry -- and one row too many leaves
#: ``DataTable.max_scroll_y`` at 1, letting the table scroll itself out of step
#: with the panel (measured on a 100k-entry directory of long names). Reserving
#: the row keeps the fit independent of that, at the cost of one blank line.
SCROLLBAR_RESERVE = 1

#: The panel actions the properties table's own cursor keys must win instead.
#:
#: These are the actions behind the six ``priority`` cursor bindings, which the
#: framework resolves from the app downwards, ahead of the focused widget's own
#: bindings. The properties table binds the same six keys for its own cursor, so
#: while it is the focused widget these are reported as disabled.
CURSOR_ACTIONS: frozenset[str] = frozenset(
    {"cursor_up", "cursor_down", "page_up", "page_down", "scroll_home", "scroll_end"}
)

#: Seconds of inactivity after which the type-ahead prefix is forgotten.
TYPEAHEAD_TIMEOUT = 1.0


class TypeAhead:
    """Accumulated type-ahead prefix and the name-order index it points at.

    Each typed character is folded into a prefix and used to locate the first
    name (in the caller's name-ordered list) that begins with it. Repeating the
    same character advances to the next matching name instead of lengthening the
    prefix. When no name shares the prefix, the tracker resets and reports
    ``None`` so the caller can fall back to the next entry in alphabetical
    order. The prefix is dropped after :attr:`timeout` seconds of inactivity.
    """

    def __init__(self, timeout: float = TYPEAHEAD_TIMEOUT) -> None:
        self.timeout = timeout
        self._prefix = ""
        self._last_char = ""
        self._match = -1
        self._stamp = 0.0

    def reset(self) -> None:
        """Forget the prefix, the last character and the last match."""
        self._prefix = ""
        self._last_char = ""
        self._match = -1
        self._stamp = 0.0

    def expired(self, now: float) -> bool:
        """True once the prefix has been idle for longer than :attr:`timeout`."""
        return bool(self._prefix) and now - self._stamp > self.timeout

    def advance(self, character: str, names: list[str], now: float) -> int | None:
        """Consume ``character`` and return the matching index, or ``None``.

        ``None`` means no name shares the (new) prefix: the caller moves to the
        next entry in alphabetical order instead. ``names`` is expected to be in
        name order, so the index returned is a name-order index.
        """
        if self.expired(now):
            self.reset()
        folded = character.casefold()
        if not folded:
            return None
        self._stamp = now
        if folded == self._last_char:
            start = self._match + 1
        else:
            self._prefix += folded
            self._last_char = folded
            start = 0
        target = file_table.prefix_match_index(names, self._prefix, start)
        if target is None:
            self.reset()
            return None
        self._match = target
        return target


class LocationChanged(Message):
    """The path the Files page shows changed (navigated, or the active pane moved).

    The persistent header keeps a path in it (FR-501) while the listing decides
    what that path is, so the page announces the change instead of the header
    reaching into the panels to find out.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        super().__init__()


class FilePanel(Vertical):
    """One side of the dual-pane layout: a directory/viewer tabsheet (ADR-011)."""

    DEFAULT_CLASSES = "file-panel"

    # ``priority`` keeps these ahead of the DataTable's own cursor bindings: the
    # table may only hold one viewport of rows, so it cannot follow the cursor
    # past its own last row. The panel owns the virtual cursor instead.
    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("pageup", "page_up", "Page up", show=False, priority=True),
        Binding("pagedown", "page_down", "Page down", show=False, priority=True),
        Binding("home", "scroll_home", "Top", show=False, priority=True),
        Binding("end", "scroll_end", "Bottom", show=False, priority=True),
    ]

    def __init__(self, name: str, path: Path) -> None:
        self.panel_name = name
        self.directory = Path(path)
        self.entries: list[DirEntry] = []
        self.visible_entries: list[DirEntry] = []
        self.filter_text = ""
        self.sort_key = "name"
        self.sort_reverse = False
        self._listing = True
        # Paths the user marked with ``space``; the batch operations act on
        # these instead of the single row under the cursor (AC4).
        self._selected: set[Path] = set()
        # Virtual scroll state: ``view_offset`` is the first row on screen and
        # ``window_start``/``window_stop`` the slice of ``visible_entries`` the
        # table actually holds. Everything between them is the viewport.
        # (``offset`` itself is a Textual layout property, hence the name.)
        self.view_offset = 0
        self.window_start = 0
        self.window_stop = 0
        #: Accumulated type-ahead prefix (FR-109); reset on navigation and idle.
        self._typeahead = TypeAhead()
        # The child widgets are built here rather than in ``compose`` so the
        # panel can hand out stable references before it is mounted.
        self._breadcrumb = Static("", id=f"breadcrumb-{name}", classes="breadcrumb")
        self._table = DataTable(id=f"table-{name}", cursor_type="row", zebra_stripes=True)
        self._mount_point = Container(id=f"mount-{name}", classes="panel-mount")
        # The two-tab tabsheet (ADR-011): the directory tab is always visible,
        # the viewer tab is hidden until a text file is opened (DC-010).
        self._tabs = TabbedContent(initial=f"directory-{name}", id=f"tabs-{name}")
        self._directory_pane = TabPane("Directory", id=f"directory-{name}")
        self._viewer_pane = TabPane("Viewer", id=f"viewer-{name}")
        super().__init__(id=f"panel-{name}")

    # -- queries -----------------------------------------------------------

    @property
    def breadcrumb(self) -> Static:
        """The breadcrumb widget of this panel."""
        return self._breadcrumb

    @property
    def table(self) -> DataTable:
        """The listing table of this panel."""
        return self._table

    @property
    def mount_point(self) -> Container:
        """Stable mount point the internal viewer will mount into (ADR-010)."""
        return self._mount_point

    @property
    def tabs(self) -> TabbedContent:
        """The two-tab tabsheet of this panel (directory + viewer, ADR-011)."""
        return self._tabs

    @property
    def directory_tab(self) -> TabPane:
        """The always-visible directory tab of this panel."""
        return self._directory_pane

    @property
    def viewer_tab(self) -> TabPane:
        """The conditional viewer tab of this panel."""
        return self._viewer_pane

    @property
    def viewer_tab_visible(self) -> bool:
        """True while the viewer tab is shown (a text file is open, DC-010)."""
        return not self._tabs.get_tab(self._viewer_pane.id or "").has_class("-hidden")

    @property
    def mounted_viewer(self) -> Widget | None:
        """The widget currently mounted in this panel's mount point, if any."""
        children = self._mount_point.children
        return children[0] if children else None

    def mount_viewer(self, widget: Widget) -> None:
        """Show ``widget`` in this panel's Viewer tab, replacing what was there.

        The mount point is a widget inside the Viewer tab, never a screen, which
        is how the viewer stays in the pane (AC1) instead of covering the
        terminal. Opening a file also reveals the Viewer tab and brings it to
        the front (DC-010, AC4).

        The existing viewer is emptied *without* hiding the tab: hiding the tab
        we are about to re-show would make ``Tabs.hide`` auto-select the Directory
        tab and queue a ``TabActivated`` that lands after this method returns,
        flipping the panel back to Directory (DC-015). :meth:`clear_viewer` is
        what hides the tab, and only when the viewer is actually closed.
        """
        self._empty_mount()
        self._mount_point.mount(widget)
        self._mount_point.add_class("visible")
        self._tabs.show_tab(self._viewer_pane.id or "")
        self._tabs.active = self._viewer_pane.id or ""

    def clear_viewer(self) -> None:
        """Empty the mount point, hide the Viewer tab and return to Directory."""
        self._empty_mount()
        if self.is_mounted:
            self._tabs.hide_tab(self._viewer_pane.id or "")

    def _empty_mount(self) -> None:
        """Empty the mount point without touching the tabsheet.

        Split out of :meth:`clear_viewer` so :meth:`mount_viewer` can replace a
        viewer's content without hiding (and so without re-selecting Directory,
        then racing back to Viewer) the tab that holds it.
        """
        if self._mount_point.children:
            self._mount_point.remove_children()
        self._mount_point.remove_class("visible")

    def previous_tab(self) -> None:
        """Move this panel's tabsheet one tab back (keyboard switching, DC-010)."""
        self._tabs.query_one(Tabs).action_previous_tab()

    def next_tab(self) -> None:
        """Move this panel's tabsheet one tab forward (keyboard switching, DC-010)."""
        self._tabs.query_one(Tabs).action_next_tab()

    @property
    def row_count(self) -> int:
        """Number of rows currently materialised in the table (always bounded)."""
        return self.table.row_count

    @property
    def total_count(self) -> int:
        """Number of entries the listing holds, materialised or not."""
        return len(self.visible_entries)

    @property
    def viewport_rows(self) -> int:
        """How many rows of the listing fit on screen, header excluded.

        Derived from the table's *height*, which does not change when rows are
        added, less the header and :data:`SCROLLBAR_RESERVE`. Materialising
        exactly this many keeps ``DataTable.max_scroll_y`` at zero, so the table
        can never scroll independently of :attr:`view_offset` -- the panel, not
        the table, owns the vertical position.
        """
        table = self.table
        header = table.header_height if table.show_header else 0
        return max(1, table.size.height - header - SCROLLBAR_RESERVE)

    @property
    def window_size(self) -> int:
        """How many listing rows are materialised in the table."""
        return self.window_stop - self.window_start

    @property
    def local_cursor(self) -> int:
        """The table cursor as an index into the materialised window."""
        if self.table.row_count <= 0:
            return 0
        return max(0, min(self.table.cursor_row, self.table.row_count - 1))

    @property
    def cursor_index(self) -> int:
        """The table cursor as an index into the whole, filtered listing."""
        return self.window_start + self.local_cursor

    @property
    def loading(self) -> bool:
        """True while the panel is scanning or (re)building its rows.

        Deliberately not Textual's ``Widget.loading`` reactive: that one covers
        the panel with a spinner and takes it out of the focus chain, neither of
        which is wanted while a listing streams in. The breadcrumb reports the
        state instead.
        """
        return self._listing

    def visible_names(self) -> list[str]:
        """Names of the rows the table currently shows."""
        return [str(self.table.get_row_at(index)[0]) for index in range(self.table.row_count)]

    def selected_entry(self) -> DirEntry | None:
        """The entry under the table cursor, or None when the cursor is off the rows."""
        if not self.visible_entries or self.table.row_count <= 0:
            return None
        index = self.cursor_index
        if 0 <= index < len(self.visible_entries):
            return self.visible_entries[index]
        return None

    def cursor_entry(self) -> DirEntry | None:
        """The entry under the table cursor -- the single-row operation target."""
        return self.selected_entry()

    @property
    def manager(self) -> FileManager:
        """The app-wide file manager, the only thing allowed to touch the disk."""
        return self.app.file_manager  # type: ignore[attr-defined,no-any-return]

    @property
    def page(self) -> "FilesPage":
        """The page this panel belongs to."""
        return self.app.query_one(FilesPage)

    def name_at(self, index: int) -> str:
        """Return the display name of listing row ``index``, materialised or not."""
        if 0 <= index < len(self.visible_entries):
            entry = self.visible_entries[index]
            return str(
                file_table.build_row(entry, selected=entry.path in self._selected)[0]
            )
        return ""

    # -- lifecycle ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        """The tabsheet: a directory tab and a conditional viewer tab (ADR-011)."""
        with self._tabs:
            with self._directory_pane:
                yield self._breadcrumb
                yield self._table
            with self._viewer_pane:
                yield self._mount_point

    def focus_table(self) -> None:
        """Give the keyboard to this panel's table; a no-op before it is mounted."""
        if self.is_mounted:
            self.table.focus()

    def properties_table_focused(self) -> bool:
        """True while the properties table owns the keyboard, in either panel (AC6)."""
        focused = self.app.focused
        return isinstance(focused, DataTable) and isinstance(focused.parent, PropertiesView)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        """Hand the cursor keys back to the focused properties table (AC6).

        The six cursor bindings above are ``priority``, and the framework resolves
        those from the app down *before* the focused widget's own bindings -- in
        either panel, since a binding is not scoped to the pane it sits in. That
        is what keeps the virtual listing cursor ahead of the panel's DataTable,
        but it would also swallow ``up``/``down``/``pageup``/``pagedown``/``home``/
        ``end`` while the *properties* table is the focused widget, leaving only
        its first field reachable by keyboard. Reporting these actions as disabled
        lets the key fall through to the table, which binds the same six keys.
        """
        del parameters
        if action in CURSOR_ACTIONS and self.properties_table_focused():
            return False
        return True

    def on_mount(self) -> None:
        """Declare the columns, hide the Viewer tab, then start the first listing.

        The viewer tab stays hidden until a text file is opened (DC-010, AC3);
        the directory tab is the one the panel opens on.
        """
        self._sync_headers()
        self.reload()
        self._tabs.hide_tab(self._viewer_pane.id or "")

    def _sync_headers(self) -> None:
        """Render the sort indicator into the column headers (ADR-012).

        The labels come from the table builder, so the widget only draws what
        the sort key and direction say. Rebuilding the columns is how a Textual
        ``DataTable`` relabels a header; it is skipped while the labels already
        match, so a plain re-filter does not churn the table.
        """
        labels = file_table.column_labels(self.sort_key, self.sort_reverse)
        current = tuple(column.label.plain for column in self.table.ordered_columns)
        if current == tuple(label for label, _ in labels):
            return
        self.table.clear(columns=True)
        for label, width in labels:
            self.table.add_column(label, width=width or None)

    def on_resize(self, event: Resize) -> None:
        """Re-materialise the window: a resize changes how many rows fit."""
        self.show_range(self.view_offset, self.cursor_index)

    # -- type-ahead (FR-109) ------------------------------------------------

    def _typeahead_enabled(self) -> bool:
        """True only while this panel's table owns the keyboard (AC6).

        The filter input, the form screens and the other panel all leave the
        keyboard elsewhere, so the type-ahead prefix must not accumulate then.
        """
        return self.app.focused is self.table

    def _typeahead_order(self) -> list[int]:
        """Display indices of the visible entries in case-insensitive name order.

        Type-ahead is name-based (FR-109), so a match found in this order is
        mapped back to the row the table actually shows. That is what makes the
        alphabetical fallback hold under a size/time/type sort, where listing
        order and name order disagree.
        """
        return file_table.name_order(self.visible_entries)

    def _next_by_name(self, order: list[int]) -> int:
        """Display index of the entry after the cursor in name order.

        An empty order means an empty listing, where the cursor is already at the
        only valid index, so ``0`` is returned.
        """
        if not order:
            return 0
        current = self.cursor_index
        rank = order.index(current) if current in order else -1
        return order[min(rank + 1, len(order) - 1)]

    def on_key(self, event: Key) -> None:
        """Consume a printable keystroke as type-ahead (FR-109).

        Only while this panel's table has the keyboard, and only for keys no
        action claims: after DC-012 no printable letter is bound, so every letter
        reaches the type-ahead while ``/``, ``space`` and the modified keys keep
        their meaning (AC6).
        """
        if not self._typeahead_enabled():
            return
        character = event.character
        if not character or not character.isprintable() or character.isspace():
            return
        if event.key in self.app.active_bindings:
            return
        if self.typeahead_char(character):
            event.stop()

    def typeahead_char(self, character: str) -> bool:
        """Move the cursor to the type-ahead match for ``character``.

        The search runs over a name-ordered view of the visible entries, so a
        prefix match jumps to the alphabetically first such entry and a repeated
        keystroke advances through the matches. When nothing shares the prefix,
        the cursor moves to the next entry in alphabetical order. Returns True
        always: a printable key the listing claimed is consumed either way.
        """
        order = self._typeahead_order()
        names = [self.visible_entries[index].name for index in order]
        target = self._typeahead.advance(character, names, monotonic())
        if target is None:
            self.move_cursor(self._next_by_name(order))
        else:
            self.move_cursor(order[target])
        return True

    # -- listing -----------------------------------------------------------

    def reload(self) -> None:
        """Re-read the directory off the event loop and refill the table.

        A rebuilt listing invalidates any half-typed prefix, so a copy, delete or
        refresh cannot leave the cursor jumping to a stale match (FR-109).
        """
        self.entries = []
        self.visible_entries = []
        self._listing = True
        self._typeahead.reset()
        self.show_range(0, 0)
        self.scan()

    def refresh_view(self) -> None:
        """Re-apply the current filter/sort without re-reading the directory."""
        self._listing = True
        self.update_breadcrumb()
        self.derive()

    @work(thread=True, exclusive=True, group="scan")
    def scan(self) -> None:
        """Read the directory in a worker thread so the UI never blocks."""
        manager: FileManager = self.app.file_manager  # type: ignore[attr-defined]
        rows = list(manager.iter_entries(self.directory))
        if manager.can_ascend(self.directory):
            rows.insert(0, DirEntry.parent(self.directory))
        self.app.call_from_thread(self.set_entries, rows)

    def set_entries(self, rows: list[DirEntry]) -> None:
        """Cache a scanned directory and derive the current view from it."""
        self.entries = rows
        self.derive()

    @work(thread=True, exclusive=True, group="view")
    def derive(self) -> None:
        """Filter and sort in a worker thread, so a 100k sort never blocks."""
        visible = file_table.chain_entries(
            self.entries, self.filter_text, self.sort_key, self.sort_reverse
        )
        self.app.call_from_thread(self.apply_entries, visible)

    def apply_entries(self, visible: list[DirEntry]) -> None:
        """Install an ordered listing and show it from the top."""
        self.visible_entries = visible
        # A re-filter or a re-sort must not keep marks pointing at rows the
        # user can no longer see, or a batch op would act on a stale path.
        # With nothing marked there is nothing to prune, and skipping the walk
        # keeps a 100k listing off the event loop for no reason.
        if self._selected:
            self._selected.intersection_update(entry.path for entry in visible)
        self._listing = False
        # The sort may have changed (a header click, ``ctrl+s``/``ctrl+r``), so the
        # header indicator is refreshed before the rows are rebuilt.
        self._sync_headers()
        # A new listing replaces the rows behind the same window bounds (a
        # re-sort, a new filter), so the rebuild cannot be skipped here.
        self.show_range(0, 0, force=True)

    # -- the virtual window ------------------------------------------------

    def show_range(self, offset: int, cursor: int, *, force: bool = False) -> None:
        """Materialise the rows around ``offset`` and put the cursor on ``cursor``.

        Both arguments are indices into :attr:`visible_entries`. The table is
        rebuilt only when the window it needs actually moves, or when ``force``
        says the rows behind an unchanged window are stale.
        """
        total = self.total_count
        viewport = self.viewport_rows
        offset = max(0, min(offset, max(0, total - viewport)))
        cursor = max(0, min(cursor, max(0, total - 1)))
        start, stop = file_table.window_bounds(offset, viewport, total)
        moved = (start, stop) != (self.window_start, self.window_stop)
        if force or moved or not self.table.row_count:
            self.window_start, self.window_stop = start, stop
            self.table.clear()
            self.table.add_rows(
                file_table.build_rows(
                    self.visible_entries[start:stop],
                    selected=frozenset(self._selected),
                )
            )
        self.view_offset = offset
        if self.table.row_count:
            self.table.cursor_coordinate = Coordinate(
                max(0, min(cursor - start, self.table.row_count - 1)), 0
            )
        self.update_breadcrumb()

    def move_cursor(self, index: int) -> None:
        """Put the cursor on listing row ``index``, scrolling the window to it."""
        start, stop = self.window_start, self.window_stop
        viewport = self.viewport_rows
        offset = self.view_offset
        if index < start:
            offset = index
        elif index >= stop:
            offset = index - viewport + 1
        self.show_range(offset, index)

    def scroll_view(self, delta: int) -> None:
        """Move the virtual viewport by ``delta`` rows, dragging the cursor along."""
        offset = self.view_offset + delta
        cursor = min(max(self.cursor_index, offset), offset + self.viewport_rows - 1)
        self.show_range(offset, cursor)

    # -- filtering and sorting ---------------------------------------------

    def clear_filter(self) -> None:
        """Drop this panel's filter-as-you-type state (FR-104).

        The filter belongs to the directory it was typed in, so entering
        another one starts unfiltered. Only this panel is reset; the other pane
        keeps its own filter. The shared input -- which mirrors the active panel
        -- is emptied too, so the next ``/`` starts from nothing.

        The input is reset without letting ``Input.Changed`` reach
        :meth:`FilesPage.on_filter_changed`: that would call ``set_filter("")``
        and spawn a ``derive()`` worker while ``go_to`` is about to ``scan()``,
        and the two worker threads race. The page suppresses the one change
        message this reset produces; the panel's own ``filter_text`` is already
        reset here, so nothing needs re-deriving.
        """
        self.filter_text = ""
        page = self.page
        if page.is_ready and page.active is self and page.filter_input.value:
            page._suppress_filter_change = True
            page.filter_input.value = ""

    def set_filter(self, text: str) -> None:
        """Narrow the visible rows to ``text``."""
        self.filter_text = text
        self.refresh_view()

    def cycle_sort(self) -> None:
        """Toggle the sort key through name/size/time/type."""
        self.sort_key = file_table.next_sort_key(self.sort_key)
        self.refresh_view()

    def reverse_sort(self) -> None:
        """Reverse the current sort."""
        self.sort_reverse = not self.sort_reverse
        self.refresh_view()

    def sort_by(self, key: str) -> None:
        """Sort by ``key``: ascending, or descending when it is already active.

        This is what a header click selects (FR-105): the first click sorts the
        column ascending, a second click on the same column flips it to
        descending. A different column always starts ascending.
        """
        if key == self.sort_key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = key
            self.sort_reverse = False
        self.refresh_view()

    # -- navigation --------------------------------------------------------

    def go_to(self, path: Path) -> None:
        """Navigate this panel to ``path`` and reload it.

        Entering a directory drops the filter-as-you-type state (FR-104), so the
        new listing starts unfiltered. Only this panel is reset: the other pane
        keeps whatever filter it was showing.
        """
        self.directory = Path(path)
        # Marks belong to the directory they were made in.
        self._selected.clear()
        # So does the filter.
        self.clear_filter()
        self.reload()
        self.post_message(LocationChanged(self.directory))

    def ascend(self) -> None:
        """Navigate to the parent directory when there is one."""
        manager: FileManager = self.app.file_manager  # type: ignore[attr-defined]
        if not manager.can_ascend(self.directory):
            return
        self.go_to(manager.parent_of(self.directory))

    def focus_parent(self) -> None:
        """Move the cursor onto the ``..`` parent row without navigating (DC-017).

        Going up is an explicit two-step: ``backspace`` highlights ``..`` (row
        0 of the listing) and ``enter`` on it ascends. At the filesystem root
        there is no ``..`` row, so this is a no-op. A filter can hide ``..``,
        so an active filter is cleared first (FR-104) and the view re-derived;
        the rebuild starts from the top, which is ``..`` again.
        """
        manager: FileManager = self.app.file_manager  # type: ignore[attr-defined]
        if not manager.can_ascend(self.directory):
            return
        if self.filter_text:
            self.clear_filter()
            self.refresh_view()
            return
        self.move_cursor(0)

    def enter_selected(self) -> None:
        """Enter the selected directory, if the cursor is on one."""
        entry = self.selected_entry()
        if entry is not None and entry.is_dir:
            self.go_to(entry.path)

    # -- multi-selection (AC4) ----------------------------------------------

    @property
    def selected_count(self) -> int:
        """How many entries are marked in this panel."""
        return len(self._selected)

    def is_selected(self, entry: DirEntry) -> bool:
        """True when ``entry`` is marked."""
        return entry.path in self._selected

    def selected_entries(self) -> list[DirEntry]:
        """The marked entries, in listing order."""
        return [entry for entry in self.visible_entries if entry.path in self._selected]

    def toggle_selection(self, index: int | None = None) -> DirEntry | None:
        """Mark or unmark one row; return the row that was touched.

        ``index`` defaults to the row under the cursor. The synthetic ``..`` row
        is never markable: it navigates rather than being a thing to copy.
        """
        if index is None:
            entry = self.cursor_entry()
        elif 0 <= index < len(self.visible_entries):
            entry = self.visible_entries[index]
        else:
            return None
        if entry is None or entry.is_parent:
            return None
        if entry.path in self._selected:
            self._selected.discard(entry.path)
        else:
            self._selected.add(entry.path)
        self.update_breadcrumb()
        self.show_range(self.view_offset, self.cursor_index, force=True)
        return entry

    def select_all(self) -> int:
        """Mark every visible entry; return how many are marked now."""
        self._selected = {entry.path for entry in self.visible_entries if not entry.is_parent}
        self.update_breadcrumb()
        return self.selected_count

    def clear_selection(self) -> None:
        """Unmark everything in this panel."""
        self._selected.clear()
        self.update_breadcrumb()

    def invert_selection(self) -> int:
        """Swap marked and unmarked; return how many are marked now."""
        self._selected = {
            entry.path for entry in self.visible_entries if not entry.is_parent
        } - self._selected
        self.update_breadcrumb()
        return self.selected_count

    def operation_targets(self) -> list[DirEntry]:
        """The entries an operation applies to: the marked ones, else the cursor.

        This is the single decision that makes a command a batch: mark two rows
        with ``space`` and both are copied, marked nothing and the one row under
        the cursor is.
        """
        marked = self.selected_entries()
        if marked:
            return marked
        entry = self.cursor_entry()
        if entry is None or entry.is_parent:
            return []
        return [entry]

    # -- file operations (AC1, AC2, AC3) -----------------------------------

    def copy_to(self, destination: Path) -> list[Path]:
        """Copy the targets into ``directory`` behind a progress modal."""
        sources = [entry.path for entry in self.operation_targets()]
        if not sources:
            return []
        manager = self.manager
        self._run_operation(
            f"Copying {self._count(sources)} item(s)",
            lambda progress, cancelled: manager.copy_into(
                sources, destination, progress=progress, cancelled=cancelled
            ),
            total=len(sources),
        )
        return sources

    def move_to(self, destination: Path) -> list[Path]:
        """Move the targets into ``directory`` behind a progress modal."""
        sources = [entry.path for entry in self.operation_targets()]
        if not sources:
            return []
        manager = self.manager
        self._run_operation(
            f"Moving {self._count(sources)} item(s)",
            lambda progress, cancelled: manager.move_into(
                sources, destination, progress=progress, cancelled=cancelled
            ),
            total=len(sources),
        )
        return sources

    def prompt_create(self, *, is_dir: bool) -> None:
        """Ask for a name and create a new file or directory in this panel."""
        form = NEW_FOLDER_FORM if is_dir else NEW_FILE_FORM
        title = "New folder" if is_dir else "New file"
        self.app.push_screen(
            FormScreen(form, title=title),
            lambda values: self._create_done(values, is_dir=is_dir),
        )

    def _create_done(self, values: dict[str, str] | None, *, is_dir: bool) -> None:
        """Create the entry the new-item form asked for."""
        if not values:
            return
        try:
            self.manager.create(self.directory, values.get("name", ""), is_dir=is_dir)
        except (OSError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        self.page.reload_panels()

    def prompt_rename(self) -> None:
        """Ask for a new name and rename the entry under the cursor."""
        entry = self.cursor_entry()
        if entry is None or entry.is_parent:
            return
        form = RENAME_FORM.with_defaults(name=entry.name)
        self.app.push_screen(FormScreen(form, title="Rename"), self._rename_done)

    def _rename_done(self, values: dict[str, str] | None) -> None:
        """Apply the rename the form asked for."""
        if not values:
            return
        entry = self.cursor_entry()
        if entry is None:
            return
        try:
            self.manager.rename(entry.path, values.get("name", ""))
        except (OSError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        self.page.reload_panels()

    def prompt_delete(self, *, permanent: bool = False) -> None:
        """Confirm, then delete the targets; trash unless ``permanent`` is set.

        The confirmation comes first and the operation is only wired up in the
        answer callback, so a declined dialog cannot have deleted anything
        (AC2, AC5).
        """
        targets = self.operation_targets()
        if not targets:
            return
        paths = [entry.path for entry in targets]
        permanent_label = "Permanently delete" if permanent else "Delete"
        self.app.push_screen(
            ConfirmScreen(
                self.delete_prompt(targets, permanent=permanent),
                title=permanent_label,
                detail=self.delete_detail(targets),
                yes_label="Delete permanently" if permanent else "Move to trash",
            ),
            lambda confirmed: self._delete_done(confirmed, paths, permanent=permanent),
        )

    def _delete_done(self, confirmed: bool | None, paths: list[Path], *, permanent: bool) -> None:
        """Run the delete only when the confirmation screen said yes."""
        if not confirmed:
            return
        manager = self.manager
        self._run_operation(
            f"{'Wiping' if permanent else 'Deleting'} {len(paths)} item(s)",
            lambda progress, cancelled: manager.delete_many(
                paths, permanent=permanent, progress=progress, cancelled=cancelled
            ),
            total=len(paths),
        )

    def _run_operation(self, title: str, operation: Operation, *, total: int) -> None:
        """Push the progress modal for a long operation and refresh afterwards."""
        self.app.push_screen(ProgressScreen(title, operation, total=total), self._operation_done)

    def _operation_done(self, _result: None) -> None:
        """Re-read both panes once the progress modal is gone."""
        self.page.reload_panels()

    @staticmethod
    def _count(paths: list[Path]) -> int:
        """How many paths an operation is about; kept for readable titles."""
        return len(paths)

    def delete_prompt(self, targets: list[DirEntry], *, permanent: bool) -> str:
        """The question the confirmation modal asks before a delete."""
        if permanent:
            verb = "Permanently delete"
            tail = "This cannot be undone and does not use the trash."
        else:
            verb = "Delete"
            tail = "The items go to the OS trash; without one they are removed for good."
        if len(targets) == 1:
            return f"{verb} {targets[0].name}?  {tail}"
        return f"{verb} {len(targets)} items?  {tail}"

    def delete_detail(self, targets: list[DirEntry]) -> str:
        """The names a confirmation modal lists, so nothing is deleted unseen."""
        names = [entry.name for entry in targets]
        shown = ", ".join(names[:5])
        if len(names) > 5:
            shown += f", \u2026 and {len(names) - 5} more"
        return shown

    # -- scroll actions ----------------------------------------------------

    def action_cursor_up(self) -> None:
        """``up``: move the virtual cursor one row towards the top."""
        self.move_cursor(self.cursor_index - 1)

    def action_cursor_down(self) -> None:
        """``down``: move the virtual cursor one row towards the bottom."""
        self.move_cursor(self.cursor_index + 1)

    def action_page_up(self) -> None:
        """``pageup``: move the virtual cursor one viewport up."""
        self.move_cursor(self.cursor_index - self.viewport_rows)

    def action_page_down(self) -> None:
        """``pagedown``: move the virtual cursor one viewport down."""
        self.move_cursor(self.cursor_index + self.viewport_rows)

    def action_scroll_home(self) -> None:
        """``home``: jump to the first row of the listing."""
        self.move_cursor(0)

    def action_scroll_end(self) -> None:
        """``end``: jump to the last row of the listing."""
        self.move_cursor(self.total_count - 1)

    @on(MouseScrollDown)
    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        """Wheel down: move the virtual viewport, never the table's own scroll."""
        event.stop()
        self.scroll_view(SCROLL_STEP)

    @on(MouseScrollUp)
    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        """Wheel up: move the virtual viewport, never the table's own scroll."""
        event.stop()
        self.scroll_view(-SCROLL_STEP)

    # -- breadcrumb --------------------------------------------------------

    def update_breadcrumb(self) -> None:
        """Refresh the breadcrumb with the path, row count and selection."""
        self.breadcrumb.update(self.breadcrumb_text())

    def listing_marker(self) -> str:
        """Short status shown after the path: loading, whole listing or window."""
        if self.loading:
            return "loading..."
        if self.window_size >= self.total_count:
            return f"{self.total_count} items"
        first = self.window_start + 1
        return f"{first}-{self.window_stop} of {self.total_count} items"

    def selection_marker(self) -> str:
        """How many rows are marked, or an empty string when none are."""
        if not self.selected_count:
            return ""
        noun = "item" if self.selected_count == 1 else "items"
        return f"\u2022 {self.selected_count} {noun} selected"

    def breadcrumb_text(self) -> str:
        """Breadcrumb path plus a row count / loading marker.

        The selection count only appears once something is marked, so an
        unmarked pane reads exactly as it did before multi-select existed.
        """
        path = file_table.format_breadcrumb(self.directory)
        marked = self.selection_marker()
        text = f" {path}  \u2502  {self.listing_marker()}  "
        return f"{text}{marked}" if marked else text.rstrip()

    @on(DataTable.HeaderSelected, "DataTable")
    def on_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Clicking a column header sorts by that column (FR-105, ADR-012).

        The widget only turns the click into a sort key; the ordering itself
        stays in the table builder. The selector is the widget type, not
        ``#table-*``, for the same reason as :meth:`on_row_selected`.
        """
        event.stop()
        key = file_table.sort_key_for_column(event.column_index)
        if key is not None:
            self.sort_by(key)

    @on(DataTable.RowSelected, "DataTable")
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        """``enter`` on a row: a directory (or ``..``) is entered, a file is viewed.

        The selector is the widget type, not ``#table-*``: an id selector in
        Textual is an exact match, and the handler already only ever sees the
        rows of its own panel.
        """
        event.stop()
        entry = self.cursor_entry()
        if entry is None:
            return
        if entry.is_dir:
            self.enter_selected()
        else:
            self.page.open_view()


class FilesPage(Vertical):
    """The dual-pane Files page and its active/inactive focus model."""

    DEFAULT_CLASSES = "files-page"
    # ``id`` on every binding is the action name, which is what lets a keybinding
    # override from the Settings page be applied at runtime (DC-006, AC2).
    BINDINGS = [
        Binding("ctrl+t", "switch_panel", "Switch pane", show=True, id="switch_panel"),
        Binding("backspace", "go_up", "Up", show=False, id="go_up"),
        Binding("slash", "start_filter", "Filter", show=False, id="start_filter"),
        # DC-012: the operation keys are F-keys or Ctrl/modified keys, never a
        # printable letter, so type-ahead (DC-007) can claim every letter key.
        Binding("ctrl+s", "cycle_sort", "Sort", show=False, id="cycle_sort"),
        Binding("ctrl+r", "reverse_sort", "Reverse", show=False, id="reverse_sort"),
        # DC-019: the footer shows the frequent operations -- Copy, Move,
        # New dir, Delete -- next to the global keys; navigation, sort, filter
        # and every rare operation stay reachable only via the command palette.
        # The footer's display order is fixed by the app's ``key_hints()``.
        Binding("space", "toggle_select", "Select", show=False, id="toggle_select"),
        Binding("ctrl+a", "select_all", "Select all", show=False, id="select_all"),
        Binding("ctrl+shift+a", "clear_selection", "Select none", show=False, id="clear_selection"),
        Binding("f5", "copy_to_other", "Copy", show=True, id="copy_to_other"),
        Binding("f6", "move_to_other", "Move", show=True, id="move_to_other"),
        Binding("f7", "new_directory", "New dir", show=True, id="new_directory"),
        Binding("shift+f7", "new_file", "New file", show=False, id="new_file"),
        Binding("shift+f6", "rename", "Rename", show=False, id="rename"),
        Binding("f8", "delete", "Delete", show=True, id="delete"),
        Binding("shift+f8", "delete_permanent", "Wipe", show=False, id="delete_permanent"),
        # Viewing keys stay palette-only; the viewer header repeats them while
        # one is open.
        Binding("f3", "view", "View", show=False, id="view"),
        Binding("alt+enter", "properties", "Info", show=False, id="properties"),
        Binding("f4", "edit", "Edit", show=False, id="edit"),
        Binding("ctrl+c", "copy_property", "Copy field", show=False, id="copy_property"),
        Binding("ctrl+g", "next_match", "Next match", show=False, id="next_match"),
        Binding("ctrl+f", "search_viewer", "Find", show=False, id="search_viewer"),
        Binding("ctrl+up", "viewer_up", "Viewer up", show=False, id="viewer_up"),
        Binding("ctrl+down", "viewer_down", "Viewer down", show=False, id="viewer_down"),
        Binding("ctrl+pageup", "viewer_page_up", "Viewer page up", show=False, id="viewer_page_up"),
        Binding(
            "ctrl+pagedown",
            "viewer_page_down",
            "Viewer page down",
            show=False,
            id="viewer_page_down",
        ),
        Binding("escape", "close_viewer", "Close viewer", show=False, id="close_viewer"),
        # DC-010: the tabsheet tabs are switchable from the keyboard (AC6).
        Binding("ctrl+left", "previous_tab", "Prev tab", show=False, id="previous_tab"),
        Binding("ctrl+right", "next_tab", "Next tab", show=False, id="next_tab"),
        # DC-006: ``f9`` asks for a URL and confirms it before the browser opens.
        Binding("f9", "open_link", "Link", show=False, id="open_link"),
    ]

    active_panel: var[str] = var(PANEL_NAMES[0], init=False)

    def __init__(
        self,
        start_path: Path,
        file_manager: FileManager | None = None,
        config_manager: ConfigManager | None = None,
    ) -> None:
        self.start_path = Path(start_path)
        self.file_manager = file_manager or FileManager()
        self.config_manager = config_manager or ConfigManager()
        self._panels: dict[str, FilePanel] = {}
        #: The path the mounted viewer/properties view is showing, if any.
        self._view_target: Path | None = None
        #: True while a ``clear_filter()`` reset of the shared input is in
        #: flight; the one ``Input.Changed`` it produces must not re-derive.
        self._suppress_filter_change = False
        super().__init__(id="files-page")

    # -- queries -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        """The filter input plus the two side-by-side panels."""
        self._panels = {name: FilePanel(name, self.start_path) for name in PANEL_NAMES}
        yield Input(placeholder="Filter\u2026", id="files-filter")
        yield Horizontal(*self._panels.values(), classes="files-panes")

    def on_mount(self) -> None:
        """Hide the filter until asked for and hand the focus to the active panel.

        The hidden filter is not focusable either, and the focus is claimed here
        instead of left to the framework's automatic first focus: a focus claimed
        later would yank the keyboard back while the user is already typing.
        """
        self.hide_filter()
        self.focus_active()

    def focus_active(self) -> None:
        """Put the keyboard on the active panel's table while this page is visible."""
        if self.display:
            self.active.focus_table()

    @property
    def panels(self) -> list[FilePanel]:
        """Both panels, in display order."""
        return [self.panel(name) for name in PANEL_NAMES]

    def panel(self, name: str) -> FilePanel:
        """Return the panel registered under ``name``."""
        try:
            return self._panels[name]
        except KeyError:
            raise ValueError(f"unknown panel: {name!r}") from None

    @property
    def active(self) -> FilePanel:
        """The one active panel."""
        return self.panel(self.active_panel)

    @property
    def is_ready(self) -> bool:
        """True once compose has built the panels.

        Mounting a page and composing it are two steps, so the app shell -- which
        may show this page the instant it mounts it -- has to be able to tell
        "mounted" from "has panels": every panel query raises before this is True.
        """
        return bool(self._panels)

    @property
    def inactive(self) -> FilePanel:
        """The panel that is not active; the viewer mounts here (ADR-010)."""
        return self.panel(next(name for name in PANEL_NAMES if name != self.active_panel))

    @property
    def filter_input(self) -> Input:
        """The filter-as-you-type input."""
        return self.query_one("#files-filter", Input)

    def active_names(self) -> list[str]:
        """The names of the panels that carry the active class (exactly one)."""
        return [panel.panel_name for panel in self.panels if panel.has_class("active")]

    # -- active/inactive model --------------------------------------------

    def watch_active_panel(self) -> None:
        """Keep the active/inactive classes in sync with the focus model."""
        self.sync_active()

    def sync_active(self) -> None:
        """Mark the active panel visually and give it the keyboard."""
        for panel in self.panels:
            is_active = panel.panel_name == self.active_panel
            panel.set_class(is_active, "active")
            panel.set_class(not is_active, "inactive")
        self.active.focus_table()

    def set_active_panel(self, name: str) -> None:
        """Make ``name`` the active panel; unknown names are rejected."""
        if name not in PANEL_NAMES:
            raise ValueError(f"unknown panel: {name!r}")
        if name != self.active_panel:
            self.active_panel = name
        else:
            self.sync_active()
        # The active pane is the one the header reports a path for, so a switch
        # moves that path even though no directory changed.
        self.post_message(LocationChanged(self.active.directory))

    def switch_panel(self) -> None:
        """Move the active/inactive split to the other panel."""
        self.set_active_panel(next(n for n in PANEL_NAMES if n != self.active_panel))

    # -- actions -----------------------------------------------------------

    def _filter_has_focus(self) -> bool:
        """True while the filter input owns the keyboard."""
        return self.filter_input.has_focus

    def show_filter(self) -> None:
        """Reveal the filter input and give it the keyboard."""
        self.filter_input.display = True
        self.filter_input.can_focus = True
        self.filter_input.focus()

    def hide_filter(self) -> None:
        """Hide the filter input and give the keyboard back to the active panel."""
        self.filter_input.display = False
        self.filter_input.can_focus = False
        self.sync_active()

    def action_switch_panel(self) -> None:
        """``ctrl+t``: swap which panel is active."""
        self.switch_panel()

    def action_go_up(self) -> None:
        """``backspace``: highlight the ``..`` parent row; ``enter`` ascends (DC-017)."""
        if not self._filter_has_focus():
            self.active.focus_parent()

    def action_start_filter(self) -> None:
        """``/``: reveal the filter input and focus it."""
        if not self._filter_has_focus():
            self.show_filter()

    def action_cycle_sort(self) -> None:
        """``ctrl+s``: cycle the active panel through name/size/time/type sorting."""
        if not self._filter_has_focus():
            self.active.cycle_sort()

    def action_reverse_sort(self) -> None:
        """``ctrl+r``: reverse the active panel's current sort."""
        if not self._filter_has_focus():
            self.active.reverse_sort()

    # -- multi-selection (AC4) ----------------------------------------------

    def action_toggle_select(self) -> None:
        """``space``: mark or unmark the active panel's cursor row."""
        if not self._filter_has_focus():
            self.active.toggle_selection()

    def action_select_all(self) -> None:
        """``ctrl+a``: mark every row the active panel shows."""
        if not self._filter_has_focus():
            self.active.select_all()

    def action_clear_selection(self) -> None:
        """``ctrl+shift+a``: unmark everything in the active panel."""
        if not self._filter_has_focus():
            self.active.clear_selection()

    # -- file operations (AC1, AC2, AC3) -----------------------------------

    def action_copy_to_other(self) -> None:
        """``f5``: copy the active panel's targets into the other pane."""
        if not self._filter_has_focus():
            self.active.copy_to(self.inactive.directory)

    def action_move_to_other(self) -> None:
        """``f6``: move the active panel's targets into the other pane."""
        if not self._filter_has_focus():
            self.active.move_to(self.inactive.directory)

    def action_new_directory(self) -> None:
        """``f7``: create a directory in the active panel."""
        if not self._filter_has_focus():
            self.active.prompt_create(is_dir=True)

    def action_new_file(self) -> None:
        """``shift+f7``: create a file in the active panel."""
        if not self._filter_has_focus():
            self.active.prompt_create(is_dir=False)

    def action_rename(self) -> None:
        """``shift+f6``: rename the entry under the active panel's cursor."""
        if not self._filter_has_focus():
            self.active.prompt_rename()

    def action_delete(self) -> None:
        """``f8``: confirm, then move the targets to the OS trash."""
        if not self._filter_has_focus():
            self.active.prompt_delete(permanent=False)

    def action_delete_permanent(self) -> None:
        """``shift+f8``: confirm, then delete the targets for good, bypassing the trash."""
        if not self._filter_has_focus():
            self.active.prompt_delete(permanent=True)

    def reload_panels(self) -> None:
        """Re-read every panel, after something changed the directories."""
        for panel in self.panels:
            panel.reload()

    # -- viewing and editing (AC1-AC6) -------------------------------------

    @property
    def manager(self) -> FileManager:
        """The app-wide manager: the only thing allowed to touch the disk."""
        return self.app.file_manager  # type: ignore[attr-defined,no-any-return]

    @property
    def mounted_widget(self) -> Widget | None:
        """The widget a panel is currently showing in its mount point, if any."""
        for panel in self.panels:
            widget = panel.mounted_viewer
            if widget is not None:
                return widget
        return None

    @property
    def viewer(self) -> ViewerPanel | None:
        """The internal content viewer, when one is mounted."""
        widget = self.mounted_widget
        return widget if isinstance(widget, ViewerPanel) else None

    @property
    def properties_view(self) -> PropertiesView | None:
        """The properties view, when one is mounted."""
        widget = self.mounted_widget
        return widget if isinstance(widget, PropertiesView) else None

    @property
    def view_target(self) -> Path | None:
        """The path the mounted viewer/properties view is showing."""
        return self._view_target

    @property
    def viewer_pane(self) -> str | None:
        """Which pane holds the viewer: always the inactive one (AC1)."""
        for panel in self.panels:
            if panel.mounted_viewer is not None:
                return panel.panel_name
        return None

    def target_entry(self) -> DirEntry | None:
        """The entry the viewer and the editor act on, or None when there is none."""
        entry = self.active.cursor_entry()
        if entry is None or entry.is_parent:
            return None
        return entry

    def open_view(self) -> Path | None:
        """Show the entry under the cursor in the internal viewer (AC1-AC3).

        The viewer always goes into the *inactive* pane and is never pushed as a
        screen, so the listing being browsed is never covered. A binary file is
        never rendered inline: it goes to the OS default application, but only
        after the launch has been confirmed (AC4 of DC-004, AC1 of DC-006).
        """
        entry = self.target_entry()
        if entry is None:
            return None
        if entry.is_dir:
            self.notify("browse a directory, or put the cursor on a file", severity="warning")
            return None
        kind = self.manager.view_kind(entry.path)
        if kind == "binary":
            self.prompt_launch(self.manager.describe_open(entry.path))
            return None
        target = self.inactive
        self._clear_other_viewers(target)
        self._view_target = entry.path
        target.mount_viewer(ViewerPanel(entry.path, manager=self.manager, kind=kind))
        return entry.path

    def open_properties(self) -> Path | None:
        """Show the detail/properties view for the entry under the cursor (AC6)."""
        entry = self.target_entry()
        if entry is None:
            return None
        target = self.inactive
        self._clear_other_viewers(target)
        self._view_target = entry.path
        target.mount_viewer(PropertiesView(entry.path, manager=self.manager))
        return entry.path

    def clear_viewers(self) -> None:
        """Empty every mount point and forget the view target."""
        for panel in self.panels:
            panel.clear_viewer()
        self._view_target = None

    def _clear_other_viewers(self, keep: FilePanel) -> None:
        """Empty every mount point except ``keep``'s, so only one viewer survives.

        Opening a view clears the *other* pane so a viewer left behind by an
        active-panel switch cannot stay on screen; the target pane's own viewer
        is replaced by :meth:`FilePanel.mount_viewer`. Clearing the target through
        :meth:`clear_viewers` instead would hide the Viewer tab and race the new
        one back to Directory (DC-015).
        """
        for panel in self.panels:
            if panel is not keep:
                panel.clear_viewer()

    def edit_target(self) -> None:
        """Edit the entry under the cursor by type (AC5, FR-304).

        Text-based files go to ``$EDITOR`` (vim by default) and everything else to
        the OS default application. The editor owns the terminal while it runs, so
        the app is suspended around the call and the view is reloaded after it --
        otherwise the viewer would still show the file as it was before the edit.

        An edit that hands the file to *another application* is a launch, so it
        confirms first (DC-006, FR-403): ``f4`` on a PDF asks before the PDF
        viewer opens. ``$EDITOR`` runs inside this terminal, so it is not an
        external hand-off and needs no confirmation.
        """
        entry = self.target_entry()
        if entry is None:
            return
        if entry.is_dir:
            self.notify("a directory has nothing to edit", severity="warning")
            return
        request = self.manager.describe_edit(entry.path)
        if not request.is_editor:
            self.prompt_launch(request)
            return
        self._edit_now(entry.path)

    def _edit_now(self, path: Path) -> None:
        """Run the edit dispatch for ``path`` and refresh what is showing it."""
        widget = self._widget_for(path)
        dispatch = self._run_suspended(path)
        program = dispatch.command[0] if dispatch.command else "?"
        self.notify(f"{path.name} → {program}")
        if isinstance(widget, ViewerPanel):
            widget.load()
        elif isinstance(widget, PropertiesView):
            widget.read_properties()

    def _run_suspended(self, path: Path) -> EditDispatch:
        """Hand ``path`` to its editor with the terminal released to the editor.

        A terminal that cannot be suspended (Textual Web, some multiplexers)
        still gets the editor: the app keeps drawing behind it, which beats
        refusing to edit at all.
        """
        try:
            with self.app.suspend():
                return self.manager.edit(path)
        except SuspendNotSupported:
            return self.manager.edit(path)

    # -- external launches (DC-006, AC1) -----------------------------------

    def prompt_launch(self, request: LaunchRequest) -> None:
        """Confirm ``request`` and run it only if the user says yes (FR-403).

        The launch is wired into the answer callback and nowhere else, so a
        declined dialog cannot have opened anything -- that is the property the
        card's risk is about, and it is why nothing in this page calls
        ``open_external`` or ``open_link`` directly.
        """
        self.app.push_screen(
            LaunchScreen(request),
            lambda confirmed: self._launch_done(confirmed, request),
        )

    def _launch_done(self, confirmed: bool | None, request: LaunchRequest) -> None:
        """Run the confirmed launch; report a refusal rather than doing nothing."""
        if not confirmed:
            self.notify("not launched")
            return
        self.manager.launch(request)
        self.notify(f"{request.program}: {request.target}")

    def prompt_link(self, url: str | None = None) -> None:
        """Ask for a URL when there is none, then confirm it before opening it."""
        if url:
            self.open_link_confirmed(url)
            return
        self.app.push_screen(
            FormScreen(LINK_FORM, title="Open link"),
            lambda values: self._link_entered(values),
        )

    def _link_entered(self, values: dict[str, str] | None) -> None:
        """Validate what the URL form returned and ask before opening it."""
        if not values:
            return
        url = values.get("url", "").strip()
        if not is_url(url):
            self.notify(f"not a URL: {url}", severity="warning")
            return
        self.open_link_confirmed(url)

    def open_link_confirmed(self, url: str) -> None:
        """Describe the browser launch for ``url`` and put it behind a confirm."""
        try:
            request = self.manager.describe_link(url)
        except ValueError as error:
            self.notify(str(error), severity="warning")
            return
        self.prompt_launch(request)

    def link_under_cursor(self) -> str | None:
        """The URL in the file under the cursor, when it holds one (FR-402)."""
        entry = self.target_entry()
        if entry is None or entry.is_dir:
            return None
        return self.manager.url_in_file(entry.path)

    def _widget_for(self, path: Path) -> Widget | None:
        """Return the mounted widget showing ``path``, if there is one."""
        for panel in self.panels:
            widget = panel.mounted_viewer
            if widget is not None and getattr(widget, "path", None) == path:
                return widget
        return None

    # -- viewer actions ----------------------------------------------------

    def _scroll_viewer(self, lines: int, pages: int = 0) -> None:
        """Move the viewer by ``lines`` lines or ``pages`` screenfuls.

        A rendered Markdown body scrolls itself; every other kind scrolls the
        viewer's own window, so a file of any size moves without being read.
        """
        viewer = self.viewer
        if viewer is None:
            return
        if pages:
            lines = pages * viewer.window_rows
        if not viewer.is_rendered:
            viewer.scroll_lines(lines)
            return
        body = viewer.body
        for _ in range(abs(lines)):
            if lines > 0:
                body.scroll_down(animate=False)
            else:
                body.scroll_up(animate=False)

    def action_view(self) -> None:
        """``f3``: open the internal viewer in the inactive panel."""
        if not self._filter_has_focus():
            self.open_view()

    def action_properties(self) -> None:
        """``alt+enter``: open the properties view in the inactive panel."""
        if not self._filter_has_focus():
            self.open_properties()

    def action_edit(self) -> None:
        """``f4``: edit the cursor entry by type (``$EDITOR`` or the OS app)."""
        if not self._filter_has_focus():
            self.edit_target()

    def action_close_viewer(self) -> None:
        """``escape``: drop the find box, else empty the mount points.

        The find box is the innermost layer, so the first escape throws it away
        and the second one leaves the file. A stray escape never loses the file
        you were reading.
        """
        if self._filter_has_focus():
            return
        viewer = self.viewer
        if isinstance(viewer, ViewerPanel) and viewer.search_open:
            viewer.close_search()
            return
        self.clear_viewers()

    def tab_panel(self) -> FilePanel:
        """The panel whose tabsheet the tab keys move (DC-010, AC6).

        The viewer lives in whichever pane holds it -- normally the inactive
        one (ADR-010) -- so that is the tabsheet the keys switch. With no
        viewer open the inactive pane is still the one a viewer would land in,
        and switching its single visible tab is a harmless no-op.
        """
        name = self.viewer_pane
        return self.panel(name) if name else self.inactive

    def action_previous_tab(self) -> None:
        """``ctrl+left``: move the viewer pane's tabsheet one tab back (DC-010, AC6)."""
        if not self._filter_has_focus():
            self.tab_panel().previous_tab()

    def action_next_tab(self) -> None:
        """``ctrl+right``: move the viewer pane's tabsheet one tab forward (DC-010, AC6)."""
        if not self._filter_has_focus():
            self.tab_panel().next_tab()

    def action_search_viewer(self) -> None:
        """``ctrl+f``: search inside the mounted viewer."""
        if not self._filter_has_focus():
            viewer = self.viewer
            if viewer is not None:
                viewer.open_search()

    def action_next_match(self) -> None:
        """``ctrl+g``: jump to the next search match in the viewer."""
        if not self._filter_has_focus():
            viewer = self.viewer
            if viewer is not None:
                viewer.next_hit()

    def action_open_link(self) -> None:
        """``f9``: open a URL with the default browser, after a confirmation.

        A file under the cursor that holds a URL is offered it directly; with
        nothing under the cursor the URL is asked for first. Either way the
        browser only opens once the confirmation says yes (FR-402, FR-403).
        """
        if self._filter_has_focus():
            return
        self.prompt_link(self.link_under_cursor())

    def action_copy_property(self) -> None:
        """``ctrl+c``: copy the value under the cursor in the properties view."""
        if self._filter_has_focus():
            return
        view = self.properties_view
        if view is not None:
            view.copy_selected()

    def action_viewer_up(self) -> None:
        """``ctrl+up``: one line up in the viewer."""
        if not self._filter_has_focus():
            self._scroll_viewer(-1)

    def action_viewer_down(self) -> None:
        """``ctrl+down``: one line down in the viewer."""
        if not self._filter_has_focus():
            self._scroll_viewer(1)

    def action_viewer_page_up(self) -> None:
        """``ctrl+pageup``: one screenful up in the viewer."""
        if not self._filter_has_focus():
            self._scroll_viewer(0, -1)

    def action_viewer_page_down(self) -> None:
        """``ctrl+pagedown``: one screenful down in the viewer."""
        if not self._filter_has_focus():
            self._scroll_viewer(0, 1)

    # -- filtering ---------------------------------------------------------

    @on(Input.Changed, "#files-filter")
    def on_filter_changed(self, event: Input.Changed) -> None:
        """Filter-as-you-type: narrow the rows of the active panel."""
        event.stop()
        if self._suppress_filter_change:
            # This change is the ``clear_filter()`` reset from a navigation:
            # the panel's filter_text was already reset directly and reload()
            # is scanning the new directory, so re-deriving here would only
            # race that scan. Swallow the message and re-arm the handler.
            self._suppress_filter_change = False
            return
        self.active.set_filter(event.value)

    @on(Input.Submitted, "#files-filter")
    def on_filter_submitted(self, event: Input.Submitted) -> None:
        """Return focus to the active panel when the filter is confirmed."""
        event.stop()
        self.hide_filter()
