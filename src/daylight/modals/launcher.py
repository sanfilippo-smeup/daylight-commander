"""The confirmation in front of every external launch (FR-403, AC1).

Nothing in this application opens a file, an application or a browser without
this screen first. It says what will open, with which application, and by which
program, and the launch itself is wired only into the answer callback -- so a
declined dialog cannot have launched anything. That is the whole point of the
card's risk: the launcher is opt-in, and :class:`~daylight.managers.external.LaunchRequest`
is the single description the confirmation and the launch are both built from.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Label

from daylight.managers.external import LaunchRequest
from daylight.modals.container import ModalContainer

__all__ = ["LAUNCH_TITLE", "LaunchScreen", "launch_detail", "launch_message"]

#: Title of every launch confirmation, whatever is being launched.
LAUNCH_TITLE = "Open externally"

#: How the head of the argv is shown: the program, then the target.
_PROGRAM_WIDTH = 12


def launch_message(request: LaunchRequest) -> str:
    """The question the modal asks about ``request``."""
    return f"Open this {request.kind} with {request.application}?"


def launch_detail(request: LaunchRequest) -> str:
    """What the modal shows underneath: the target and the program that runs."""
    program = f"{request.program:<{_PROGRAM_WIDTH}}"
    return f"{program}{request.target}"


class LaunchScreen(ModalContainer[bool]):
    """Ask before handing a target to another application (AC1)."""

    BINDINGS = [
        Binding("y", "accept", "Open", show=True),
        Binding("n", "cancel", "Cancel", show=True),
    ]

    def __init__(self, request: LaunchRequest, *, title: str = LAUNCH_TITLE) -> None:
        self.request = request
        super().__init__(title)
        # The body holds only labels about the launch, so it is not a tab stop:
        # that leaves the two answers as the keyboard order, and ``Tab`` from
        # "Open" lands on "Cancel" instead of the scrollable body (FR-403).
        self.body.can_focus = False

    def on_mount(self) -> None:
        """Open with "Open" focused: Enter confirms, Tab reaches Cancel (FR-403).

        Opening is opt-in, so the affirmative answer is the default the keyboard
        starts on -- but nothing launches from focus alone. The launch still runs
        only from the answer callback, which fires when this screen is dismissed.
        """
        self.query_one("#confirm-yes", Button).focus()

    def compose_body(self) -> ComposeResult:
        """The question, what will open, and the two answers in the footer."""
        with self.body:
            yield Label(launch_message(self.request), classes="modal-message")
            yield Label(launch_detail(self.request), classes="modal-detail")
        with self.footer:
            yield Button("Cancel", id="confirm-no", variant="default")
            yield Button("Open", id="confirm-yes", variant="primary")

    def cancelled_result(self) -> bool:
        """Backing out is a no: nothing is launched (FR-403)."""
        return False

    def detail_text(self) -> str:
        """The launch this screen is asking about, as the modal shows it.

        Exposed so a test can assert that the confirmation names the target and
        the program, rather than only that *some* modal appeared.
        """
        return launch_detail(self.request)

    def action_accept(self) -> None:
        """``y`` or the confirm button: launch, as the caller wired it up."""
        self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route the footer buttons: Open confirms, Cancel backs out."""
        event.stop()
        self.dismiss(event.button.id == "confirm-yes")
