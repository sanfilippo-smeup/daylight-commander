"""The modal that renders a declarative :class:`daylight.forms.Form` (ADR-007).

One input per visible field, generated from the schema, plus the form's own
submit and cancel labels in the footer. Hidden fields are carried in the
submitted values without ever being shown. Submitting hands the raw values back
as a dict, or ``None`` when the user backs out, so the caller decides what the
form means.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Input, Label

from daylight.forms import Form
from daylight.modals.container import ModalContainer

__all__ = ["FormScreen"]


class FormScreen(ModalContainer[dict[str, str] | None]):
    """Ask a form schema for its values."""

    def __init__(self, form: Form, *, title: str | None = None) -> None:
        self.form = form
        self._inputs: dict[str, Input] = {}
        super().__init__(title or form.name)

    def compose_body(self) -> ComposeResult:
        """One labelled input per visible field, then the form's own buttons."""
        for item in self.form.visible_fields():
            widget = Input(value=self.form.defaults().get(item.name, ""))
            if item.placeholder:
                widget.placeholder = item.placeholder
            self._inputs[item.name] = widget
            with Vertical(classes="form-field"):
                yield Label(item.label, classes="form-label")
                yield widget
                if item.help:
                    yield Label(item.help, classes="form-help")
        with self.footer:
            yield Button(self.form.cancel_label, id="form-cancel")
            yield Button(self.form.submit_label, id="form-submit", variant="primary")

    def on_mount(self) -> None:
        """Put the keyboard in the first field so typing just works."""
        first = next(iter(self._inputs.values()), None)
        if first is not None:
            first.focus()

    def values(self) -> dict[str, str]:
        """Return every field's current text, hidden fields included."""
        submitted = self.form.defaults()
        submitted.update({name: widget.value for name, widget in self._inputs.items()})
        return submitted

    def errors(self) -> dict[str, str]:
        """Return the validation errors for what is currently typed in."""
        return self.form.validate(self.values())

    def action_submit(self) -> None:
        """Hand the values back to the caller, unless the form is not valid."""
        if self.errors():
            return
        self.dismiss(self.values())

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """``enter`` in a field submits the form."""
        event.stop()
        self.action_submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """The submit button submits; the other button backs out."""
        event.stop()
        if event.button.id == "form-submit":
            self.action_submit()
        else:
            self.dismiss(None)
