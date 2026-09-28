"""Dual-pane Files page: listing, sorting, filtering, navigation and operations.

One :class:`FilePanel` per side. Exactly one panel is active at a time
(:attr:`FilesPage.active_panel`); the active panel is the focused one and is the
only one that reacts to navigation, filter and sort keys. The inactive panel
keeps a stable mount point (``.panel-mount``) for the internal viewer (ADR-010).

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
"""

from __future__ import annotations

from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.events import MouseScrollDown, MouseScrollUp, Resize
from textual.reactive import var
from textual.widgets import DataTable, Input, Static

from daylight.components import file_table
from daylight.forms import NEW_FILE_FORM, NEW_FOLDER_FORM, RENAME_FORM
from daylight.managers import ConfigManager, FileManager
from daylight.models import DirEntry
from daylight.modals import ConfirmScreen, FormScreen, ProgressScreen
from daylight.modals.progress import Operation

__all__ = ["PANEL_NAMES", "SCROLL_STEP", "SCROLLBAR_RESERVE", "FilePanel", "FilesPage"]

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


class FilePanel(Vertical):
    """One side of the dual-pane layout: breadcrumb, table and viewer mount point."""

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
        # The child widgets are built here rather than in ``compose`` so the
        # panel can hand out stable references before it is mounted.
        self._breadcrumb = Static("", id=f"breadcrumb-{name}", classes="breadcrumb")
        self._table = DataTable(id=f"table-{name}", cursor_type="row", zebra_stripes=True)
        self._mount_point = Container(id=f"mount-{name}", classes="panel-mount")
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
            return file_table.build_row(self.visible_entries[index])[0]
        return ""

    # -- lifecycle ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Breadcrumb, listing table and the viewer mount point."""
        yield self._breadcrumb
        yield self._table
        yield self._mount_point

    def focus_table(self) -> None:
        """Give the keyboard to this panel's table; a no-op before it is mounted."""
        if self.is_mounted:
            self.table.focus()

    def on_mount(self) -> None:
        """Declare the columns once, then kick off the first listing."""
        for label, width in file_table.COLUMNS:
            self.table.add_column(label, width=width or None)
        self.reload()

    def on_resize(self, event: Resize) -> None:
        """Re-materialise the window: a resize changes how many rows fit."""
        self.show_range(self.view_offset, self.cursor_index)

    # -- listing -----------------------------------------------------------

    def reload(self) -> None:
        """Re-read the directory off the event loop and refill the table."""
        self.entries = []
        self.visible_entries = []
        self._listing = True
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
            self.table.add_rows(file_table.build_rows(self.visible_entries[start:stop]))
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

    # -- navigation --------------------------------------------------------

    def go_to(self, path: Path) -> None:
        """Navigate this panel to ``path`` and reload it."""
        self.directory = Path(path)
        # Marks belong to the directory they were made in.
        self._selected.clear()
        self.reload()

    def ascend(self) -> None:
        """Navigate to the parent directory when there is one."""
        manager: FileManager = self.app.file_manager  # type: ignore[attr-defined]
        if not manager.can_ascend(self.directory):
            return
        self.go_to(manager.parent_of(self.directory))

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

    @on(DataTable.RowSelected, "DataTable")
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        """``enter`` on a directory row navigates into it.

        The selector is the widget type, not ``#table-*``: an id selector in
        Textual is an exact match, and the handler already only ever sees the
        rows of its own panel.
        """
        event.stop()
        self.enter_selected()


class FilesPage(Vertical):
    """The dual-pane Files page and its active/inactive focus model."""

    DEFAULT_CLASSES = "files-page"
    BINDINGS = [
        Binding("ctrl+t", "switch_panel", "Switch pane", show=True),
        Binding("backspace", "go_up", "Up", show=True),
        Binding("slash", "start_filter", "Filter", show=True),
        Binding("ctrl+h", "toggle_hidden", "Hidden", show=True),
        Binding("s", "cycle_sort", "Sort", show=True),
        Binding("r", "reverse_sort", "Reverse", show=True),
        # The operation keys are deliberately hidden from the footer: the bar
        # already runs out of room at 120 columns, and the navigation keys that
        # were there first are the ones a new user needs to see.
        Binding("space", "toggle_select", "Select", show=False),
        Binding("ctrl+a", "select_all", "Select all", show=False),
        Binding("a", "clear_selection", "Select none", show=False),
        Binding("f5", "copy_to_other", "Copy", show=False),
        Binding("f6", "move_to_other", "Move", show=False),
        Binding("f7", "new_directory", "New dir", show=False),
        Binding("f8", "new_file", "New file", show=False),
        Binding("n", "rename", "Rename", show=False),
        Binding("d", "delete", "Delete", show=False),
        Binding("D", "delete_permanent", "Wipe", show=False),
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
        super().__init__(id="files-page")

    # -- queries -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        """The filter input plus the two side-by-side panels."""
        self._panels = {name: FilePanel(name, self.start_path) for name in PANEL_NAMES}
        yield Input(placeholder="Filter\u2026", id="files-filter")
        yield Horizontal(*self._panels.values(), classes="files-panes")

    def on_mount(self) -> None:
        """Hide the filter until asked for and hand the focus to the active panel.

        The hidden filter is not focusable either, so the focus the framework
        hands to a freshly mounted page lands on the active panel's table rather
        than on the filter bar.
        """
        self.hide_filter()

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
        """``backspace``: navigate the active panel up one level."""
        if not self._filter_has_focus():
            self.active.ascend()

    def action_start_filter(self) -> None:
        """``/``: reveal the filter input and focus it."""
        if not self._filter_has_focus():
            self.show_filter()

    def action_cycle_sort(self) -> None:
        """``s``: cycle the active panel through name/size/time/type sorting."""
        if not self._filter_has_focus():
            self.active.cycle_sort()

    def action_reverse_sort(self) -> None:
        """``r``: reverse the active panel's current sort."""
        if not self._filter_has_focus():
            self.active.reverse_sort()

    def action_toggle_hidden(self) -> None:
        """``ctrl+h``: toggle hidden entries and persist the preference."""
        self.file_manager.show_hidden = not self.file_manager.show_hidden
        self.config_manager.update(show_hidden=self.file_manager.show_hidden)
        for panel in self.panels:
            panel.reload()

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
        """``a``: unmark everything in the active panel."""
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
        """``f8``: create a file in the active panel."""
        if not self._filter_has_focus():
            self.active.prompt_create(is_dir=False)

    def action_rename(self) -> None:
        """``n``: rename the entry under the active panel's cursor."""
        if not self._filter_has_focus():
            self.active.prompt_rename()

    def action_delete(self) -> None:
        """``d``: confirm, then move the targets to the OS trash."""
        if not self._filter_has_focus():
            self.active.prompt_delete(permanent=False)

    def action_delete_permanent(self) -> None:
        """``D``: confirm, then delete the targets for good, bypassing the trash."""
        if not self._filter_has_focus():
            self.active.prompt_delete(permanent=True)

    def reload_panels(self) -> None:
        """Re-read every panel, after something changed the directories."""
        for panel in self.panels:
            panel.reload()

    # -- filtering ---------------------------------------------------------

    @on(Input.Changed, "#files-filter")
    def on_filter_changed(self, event: Input.Changed) -> None:
        """Filter-as-you-type: narrow the rows of the active panel."""
        event.stop()
        self.active.set_filter(event.value)

    @on(Input.Submitted, "#files-filter")
    def on_filter_submitted(self, event: Input.Submitted) -> None:
        """Return focus to the active panel when the filter is confirmed."""
        event.stop()
        self.hide_filter()
