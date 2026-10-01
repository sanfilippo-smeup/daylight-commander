"""The Properties tab: every field of one entry, full page (FR-504, FR-303).

The Properties *view* of DC-004 is mounted in the inactive Files panel; this tab
mounts the same widget across the whole pane. The app picks the path
(:meth:`DaylightApp.view_target`) and calls :meth:`PropertiesPage.open_path`, and
re-opening the path already shown is a no-op, so the tab keeps what it read.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical

from daylight.components.modules.properties_view import PropertiesView
from daylight.components.shell import NoticeLine

__all__ = ["NOTHING_SELECTED", "PropertiesPage"]

#: Shown when the tab has no entry to describe.
NOTHING_SELECTED = " No entry selected — browse to a file and press i for its properties."


class PropertiesPage(Vertical):
    """The full-page properties view for the entry the app points this tab at."""

    DEFAULT_CLASSES: ClassVar[str] = "properties-page"

    BINDINGS = [
        Binding("c", "copy_field", "Copy field", show=True),
    ]

    #: Not focusable: the page drives the view, so the listing keeps the keyboard.
    can_focus = False

    def __init__(self) -> None:
        self.path: Path | None = None
        self._notice = NoticeLine()
        self._mount = Container(classes="view-mount")
        super().__init__(id="properties-page")

    @property
    def notice(self) -> NoticeLine:
        """The line that explains an empty tab."""
        return self._notice

    @property
    def view(self) -> PropertiesView | None:
        """The mounted properties view, when there is one."""
        children = self._mount.children
        return children[0] if children and isinstance(children[0], PropertiesView) else None

    def compose(self) -> ComposeResult:
        """The notice line and the properties mount point."""
        yield self._notice
        yield self._mount

    def on_mount(self) -> None:
        """Build the first properties view, if the app already had a path for us."""
        self.load_view()

    def open_path(self, path: Path | None) -> None:
        """Describe ``path``; the path already described is left as it is."""
        target = Path(path) if path is not None else None
        if target == self.path and self.is_mounted:
            return
        self.path = target
        if self.is_mounted:
            self.load_view()

    def load_view(self) -> None:
        """(Re)build the properties view for :attr:`path`."""
        self._mount.remove_children()
        if self.path is None:
            self._notice.explain(NOTHING_SELECTED)
            return
        self._notice.explain("")
        self._mount.mount(PropertiesView(self.path, manager=self.app.file_manager))

    def action_copy_field(self) -> None:
        """``c``: put the value under the cursor on the clipboard."""
        view = self.view
        if view is not None:
            view.copy_selected()
