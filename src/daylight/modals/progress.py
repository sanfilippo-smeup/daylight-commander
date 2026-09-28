"""The progress modal for long copy/move/delete runs (AC3, FR-205).

The operation is a plain callable that runs in a worker thread and gets handed
two hooks: ``progress(completed, total, current)`` and ``cancelled()``. The bar
is therefore driven by the work itself rather than by a guess, and Cancel works
by setting an event the manager checks between files -- which is what keeps the
event loop free, the same way DC-002's scan does.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.widgets import Button, Label, ProgressBar

from daylight.managers import OperationCancelled
from daylight.modals.container import ModalContainer

__all__ = ["ProgressScreen"]

#: Signature of the operation a progress screen runs.
Operation = Callable[[Callable[[int, int, Path], None], Callable[[], bool]], object]


class ProgressScreen(ModalContainer[None]):
    """Run a long operation off the event loop, with progress and Cancel."""

    def __init__(self, title: str, operation: Operation, *, total: int = 0) -> None:
        self.operation = operation
        self.total = total
        self._cancel = threading.Event()
        self._finished = threading.Event()
        self._bar = ProgressBar(total=total or None, classes="op-bar")
        self._status = Label("starting\u2026", classes="op-status")
        super().__init__(title)

    @property
    def bar(self) -> ProgressBar:
        """The progress bar this screen drives."""
        return self._bar

    @property
    def status(self) -> Label:
        """The line naming the item being worked on, or the error that stopped it."""
        return self._status

    @property
    def cancelled(self) -> bool:
        """True once the user pressed Cancel."""
        return self._cancel.is_set()

    @property
    def running(self) -> bool:
        """True while the worker is still going."""
        return not self._finished.is_set()

    def compose_body(self) -> ComposeResult:
        """The bar, the running item, and a single Cancel/Close button."""
        with self.body:
            yield self._bar
            yield self._status
        with self.footer:
            yield Button("Cancel", id="op-cancel", variant="warning")

    def on_mount(self) -> None:
        """Start the work as soon as the screen is on screen."""
        self.run_operation()

    def report(self, completed: int, total: int, current: Path) -> None:
        """Thread-safe progress sink handed to the operation."""
        self.app.call_from_thread(self.show_progress, completed, total, current)

    def show_progress(self, completed: int, total: int, current: Path) -> None:
        """Move the bar and name the item being worked on."""
        if total:
            self._bar.update(total=total)
        self._bar.update(progress=max(0, min(completed, total or completed)))
        self._status.update(f"{current.name}")

    def action_cancel(self) -> None:
        """``escape`` or the button: ask the worker to stop, or close when done."""
        if self.running:
            self._cancel.set()
            self._status.update("cancelling\u2026")
            self._cancel_button.label = "Wait"
            return
        self.dismiss(None)

    @property
    def _cancel_button(self) -> Button:
        """The one footer button, re-labelled once the work has stopped."""
        return self.query_one("#op-cancel", Button)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """The footer button is Cancel while running and Close once it is not."""
        event.stop()
        self.action_cancel()

    @work(thread=True, exclusive=True, group="operation")
    def run_operation(self) -> None:
        """Run the operation in a worker thread so the UI never blocks."""
        try:
            self.operation(self.report, self._cancel.is_set)
        except OperationCancelled:
            self.app.call_from_thread(self.stop, "cancelled")
        except OSError as error:
            self.app.call_from_thread(self.stop, f"failed: {error}")
        except Exception as error:
            # A modal must never leave the user staring at a frozen bar, so
            # anything unexpected ends the operation and says so.
            self.app.call_from_thread(self.stop, f"failed: {error}")
        else:
            self.app.call_from_thread(self.stop, "done")

    def stop(self, message: str) -> None:
        """Finish the work, report how it went, and close the modal when it is done.

        A successful operation closes itself, so the listing behind it refreshes
        without the user having to dismiss a modal about work that is already
        over. A cancel or a failure stays on screen: that is a message to read.
        """
        self._finished.set()
        self._status.update(message)
        if message == "done":
            self.dismiss(None)
            return
        self._cancel_button.label = "Close"
