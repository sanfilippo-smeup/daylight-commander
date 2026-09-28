"""The reusable modal frame every screen in :mod:`daylight.modals` sits in.

ADR-007 asks for one scrollable container with a header that closes or goes
back and a footer, hosting both forms and confirmations. The frame is built in
``__init__`` and yielded from ``compose``; a concrete screen fills it by
overriding :meth:`ModalContainer.compose_body` (and mounting buttons into
:attr:`ModalContainer.footer`), so a subclass can never accidentally drop the
frame by overriding ``compose`` itself.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label

__all__ = ["ModalContainer"]

#: What a screen hands back to its ``push_screen`` callback.
ResultT = TypeVar("ResultT", bound=object)


class ModalContainer(ModalScreen[ResultT], Generic[ResultT]):
    """A scrollable modal with a header, a body and a footer."""

    BINDINGS = [
        Binding("escape", "cancel", "Back", show=True),
    ]

    #: Header text. Named ``dialog_title`` rather than ``title`` because
    #: ``Screen`` already owns ``title`` and overwrites it when the screen is
    #: pushed -- a subclass reading it back would get ``None``.
    dialog_title = "Dialog"

    def __init__(self, title: str | None = None) -> None:
        if title is not None:
            self.dialog_title = title
        self._header = Label(self.dialog_title, classes="modal-header")
        self._body = VerticalScroll(id="modal-body", classes="modal-body")
        self._footer = Horizontal(id="modal-footer", classes="modal-footer")
        # The frame is a widget built here rather than a ``with`` block in
        # ``compose``: a subclass that overrides ``compose`` to fill the body
        # would otherwise drop the frame and the whole modal with it.
        self._frame = Vertical(self._header, self._body, self._footer, classes="modal-frame")
        super().__init__(classes="modal-container")

    @property
    def body(self) -> VerticalScroll:
        """The scrollable area a screen fills with its content."""
        return self._body

    @property
    def footer(self) -> Horizontal:
        """The button bar at the bottom of the modal."""
        return self._footer

    @property
    def heading(self) -> Label:
        """The header line, so a screen can re-word it as it goes."""
        return self._header

    def compose(self) -> ComposeResult:
        """The shared frame, then whatever the screen put inside it."""
        yield self._frame
        yield from self.compose_body()

    def compose_body(self) -> ComposeResult:
        """What the modal shows; overridden by every concrete screen."""
        return iter(())

    def set_title(self, title: str) -> None:
        """Re-word the header."""
        self.dialog_title = title
        self._header.update(title)

    def cancelled_result(self) -> ResultT:
        """The result handed back when the user backs out instead of confirming."""
        return None  # type: ignore[return-value]

    def action_cancel(self) -> None:
        """``escape``: back out, as the header back affordance does."""
        self.dismiss(self.cancelled_result())
