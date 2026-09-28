"""The confirmation modal that gates every destructive action (AC2, FR-204).

The screen answers a single question -- "are you sure?" -- and reports the
answer as a bool, so the caller runs the operation only on ``True``. Nothing
destructive can start before this screen is answered, because the caller's
operation callback is never wired up until then.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Label

from daylight.modals.container import ModalContainer

__all__ = ["ConfirmScreen"]


class ConfirmScreen(ModalContainer[bool]):
    """Ask for confirmation, and report whether the user said yes."""

    BINDINGS = [
        Binding("y", "accept", "Yes", show=True),
        Binding("n", "cancel", "No", show=True),
    ]

    def __init__(
        self,
        message: str,
        *,
        title: str = "Confirm",
        detail: str = "",
        yes_label: str = "Delete",
        no_label: str = "Cancel",
    ) -> None:
        self.message = message
        self.detail = detail
        self.yes_label = yes_label
        self.no_label = no_label
        super().__init__(title)

    def compose_body(self) -> ComposeResult:
        """The question, any detail, and the two answers in the footer."""
        with self.body:
            yield Label(self.message, classes="modal-message")
            if self.detail:
                yield Label(self.detail, classes="modal-detail")
        with self.footer:
            yield Button(self.no_label, id="confirm-no", variant="default")
            yield Button(self.yes_label, id="confirm-yes", variant="error")

    def cancelled_result(self) -> bool:
        """Backing out is a no: the operation must not run."""
        return False

    def action_accept(self) -> None:
        """``y`` or the danger button: run the operation."""
        self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route the footer buttons: the danger one confirms, the other backs out."""
        event.stop()
        self.dismiss(event.button.id == "confirm-yes")
