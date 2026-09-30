"""The detail/properties view (FR-303, AC6).

Like the viewer this is mounted into the inactive panel and is never a screen. It
shows what the manager reported -- size, inode, times, permissions, owner, group,
MIME and checksum -- as two columns, and every value is a plain string the copy
key can put on the clipboard. The checksum is read in a worker thread, so opening
properties on a large file does not freeze the listing.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from daylight.components import properties_table
from daylight.managers import FileManager
from daylight.models import FileProperties

__all__ = ["PropertiesView"]

#: The keys that drive the properties view, shown in the header.
KEY_HINTS = "c copy field \u2022 v viewer \u2022 esc close"


class PropertiesView(Vertical):
    """Stat information for one entry, with copyable fields (AC6)."""

    DEFAULT_CLASSES: ClassVar[str] = "properties"

    #: Not focusable: the listing keeps the keyboard and the page drives the view.
    can_focus = False

    def __init__(self, path: Path, *, manager: FileManager) -> None:
        self.path = Path(path)
        self.manager = manager
        self.properties: FileProperties | None = None
        self._loading = False
        self._header = Static("", classes="properties-header")
        self._table = DataTable(id="properties-table", cursor_type="row", zebra_stripes=True)
        self._status = Static("", classes="properties-status")
        super().__init__()

    # -- queries -----------------------------------------------------------

    @property
    def table(self) -> DataTable:
        """The two-column table of property rows."""
        return self._table

    @property
    def header(self) -> Static:
        """The header line: the path and the keys that drive the view."""
        return self._header

    @property
    def status(self) -> Static:
        """The line that reports progress, errors and the last copy."""
        return self._status

    @property
    def loading(self) -> bool:
        """True while the properties (and the checksum) are being read."""
        return self._loading

    @property
    def rows(self) -> list[tuple[str, str]]:
        """The property rows currently in the table."""
        if self.properties is None:
            return []
        return properties_table.build_rows(self.properties)

    @property
    def selected_value(self) -> str:
        """The value under the table cursor: what the copy key copies."""
        if self.properties is None:
            return ""
        return properties_table.copy_value(self.properties, self.table.cursor_row)

    def selected_field(self) -> str:
        """The label of the field under the table cursor."""
        rows = self.rows
        if 0 <= self.table.cursor_row < len(rows):
            return rows[self.table.cursor_row][0]
        return ""

    # -- lifecycle ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Header, the property table and the status line."""
        yield self._header
        yield self._table
        yield self._status

    def on_mount(self) -> None:
        """Declare the columns, show the header and start reading the properties."""
        for label, width in properties_table.COLUMNS:
            self._table.add_column(label, width=width or None)
        self._header.update(f" {self.path.name}  \u2502  {KEY_HINTS}")
        self._status.update(" reading\u2026")
        self._loading = True
        self.read_properties()

    # -- loading -----------------------------------------------------------

    @work(thread=True, exclusive=True, group="properties-load")
    def read_properties(self) -> None:
        """Read stat, owner, MIME and the checksum off the event loop."""
        try:
            properties = self.manager.describe(self.path)
        except OSError as error:
            self.app.call_from_thread(self.show_error, str(error))
            return
        self.app.call_from_thread(self.show_properties, properties)

    def show_error(self, message: str) -> None:
        """Report a path that cannot be described instead of showing empty fields."""
        self._loading = False
        self._status.update(f" cannot read {self.path.name}: {message}")

    def show_properties(self, properties: FileProperties) -> None:
        """Install freshly read properties and fill the table."""
        self.properties = properties
        self._loading = False
        self._table.clear()
        self._table.add_rows(self.rows)
        self._status.update(f" {len(self.rows)} fields \u2014 press c to copy the selected value")

    # -- copy --------------------------------------------------------------

    def copy_selected(self) -> str:
        """Put the selected value on the clipboard and report it."""
        value = self.selected_value
        if not value:
            self._status.update(" nothing to copy")
            return ""
        self.app.copy_to_clipboard(value)
        self._status.update(f" copied {self.selected_field()}: {value}")
        return value
