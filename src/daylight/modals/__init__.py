"""Modal screens (ADR-007).

:class:`ModalContainer` is the reusable frame every modal here sits in: a
scrollable body, a header that closes or goes back, and a footer that hosts the
buttons. The three screens built on it are pushed with ``push_screen`` and hand
their result back through the callback, so the caller never has to look inside:

* :class:`ConfirmScreen` -- the gate in front of every destructive action (AC2).
* :class:`LaunchScreen` -- the gate in front of every *external* launch: it names
  the target and the application before anything is handed over (FR-403).
* :class:`FormScreen` -- renders a :class:`daylight.forms.Form` schema.
* :class:`ProgressScreen` -- runs a long operation in a worker thread and offers
  Cancel, so the event loop keeps turning while files move (AC3).
"""

from __future__ import annotations

from daylight.modals.confirm import ConfirmScreen
from daylight.modals.container import ModalContainer
from daylight.modals.form import FormScreen
from daylight.modals.launcher import LaunchScreen
from daylight.modals.progress import ProgressScreen

__all__ = ["ConfirmScreen", "FormScreen", "LaunchScreen", "ModalContainer", "ProgressScreen"]
