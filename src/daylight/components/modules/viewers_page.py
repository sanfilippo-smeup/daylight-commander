"""The Viewers tab: one file, the whole pane (FR-504).

DC-004 mounts its viewer inside the inactive Files panel so browsing is never
covered. This tab is the other half of the same viewer widget: a full-page mount
for the file currently under the cursor, driven by the same
:class:`~daylight.managers.FileManager`, so a tab is a second mount point and
not a second implementation.

The app owns the choice of path (:meth:`DaylightApp.view_target`) and calls
:meth:`ViewersPage.open_path`; re-opening the same path is a no-op, so a tab
round trip never re-reads the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical

from daylight.components.modules.viewer import ViewerPanel
from daylight.components.shell import NoticeLine

__all__ = ["NOTHING_OPEN", "ViewersPage"]

#: Shown when the tab has nothing to read yet.
NOTHING_OPEN = " No file open — browse to a file and press v to view it here."


class ViewersPage(Vertical):
    """The full-page viewer for the file the app points this tab at."""

    DEFAULT_CLASSES: ClassVar[str] = "viewers-page"

    BINDINGS = [
        Binding("escape", "close_view", "Close", show=True),
    ]

    #: Focusable so this tab's own key reaches it: a page whose hint bar advertises
    #: a key that cannot fire is a dead end (AC3).
    can_focus = True

    def __init__(self) -> None:
        self.path: Path | None = None
        self._notice = NoticeLine()
        self._mount = Container(classes="view-mount")
        super().__init__(id="viewers-page")

    @property
    def notice(self) -> NoticeLine:
        """The line that explains an empty or unviewable tab."""
        return self._notice

    @property
    def viewer(self) -> ViewerPanel | None:
        """The mounted viewer, when there is one."""
        children = self._mount.children
        return children[0] if children and isinstance(children[0], ViewerPanel) else None

    def compose(self) -> ComposeResult:
        """The notice line and the viewer mount point."""
        yield self._notice
        yield self._mount

    def on_mount(self) -> None:
        """Build the first viewer, if the app already had a path for us."""
        self.load_view()

    def open_path(self, path: Path | None) -> None:
        """Show ``path``; the path already on screen is left exactly as it is."""
        target = Path(path) if path is not None else None
        if target == self.path and self.is_mounted:
            return
        self.path = target
        if self.is_mounted:
            self.load_view()

    def load_view(self) -> None:
        """(Re)build the viewer widget for :attr:`path`."""
        self._mount.remove_children()
        if self.path is None:
            self._notice.explain(NOTHING_OPEN)
            return
        manager = self.app.file_manager
        kind = manager.view_kind(self.path)
        # A binary file is never rendered inline (FR-305) and a directory is not
        # a document at all: both are reported instead of drawn.
        if kind in ("binary", "directory"):
            noun = "binary" if kind == "binary" else "a directory"
            self._notice.explain(f" {self.path.name} is {noun}: nothing to show here.")
            return
        self._notice.explain("")
        self._mount.mount(ViewerPanel(self.path, manager=manager, kind=kind))

    def action_close_view(self) -> None:
        """``escape``: empty the tab, leaving the Files page untouched."""
        self.open_path(None)
