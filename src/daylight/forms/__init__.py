"""Declarative form schemas (ADR-007).

A form is data, not a widget: :class:`FormField` describes one input and
:class:`Form` the set of them, and :mod:`daylight.modals.form` turns that schema
into widgets. The module holds no UI imports, so a schema can be validated in a
plain unit test with no app running.

The field kinds are the ones ADR-007 names. Only ``string``/``autocomplete``
and ``boolean`` are needed by the new-file and rename forms, but the whole set is
validated here so a later card does not have to come back and add rules.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field

__all__ = [
    "FIELD_KINDS",
    "NEW_FILE_FORM",
    "NEW_FOLDER_FORM",
    "RENAME_FORM",
    "Form",
    "FormField",
    "ValidationError",
]

#: The field kinds ADR-007 requires the schema to support, in menu order.
FIELD_KINDS: tuple[str, ...] = (
    "string",
    "number",
    "integer",
    "boolean",
    "autocomplete",
    "date",
    "hidden",
)

#: Text a boolean field accepts as true, case-insensitively.
_TRUE_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on", "y"})

#: Text a boolean field accepts as false, case-insensitively.
_FALSE_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off", "n", ""})


class ValidationError(ValueError):
    """Raised by :meth:`Form.validate` when a submitted form is not usable."""


@dataclass
class FormField:
    """One input of a form, described as data."""

    name: str
    label: str
    kind: str = "string"
    default: str = ""
    required: bool = False
    placeholder: str = ""
    help: str = ""
    choices: Sequence[str] = ()

    def __post_init__(self) -> None:
        """Reject an unknown kind and normalise the choices to a tuple."""
        if self.kind not in FIELD_KINDS:
            raise ValueError(f"unknown field kind: {self.kind!r}")
        self.choices = tuple(self.choices)

    @property
    def is_hidden(self) -> bool:
        """True when the field is carried but not shown to the user."""
        return self.kind == "hidden"

    def text(self, value: object) -> str:
        """Return ``value`` as the string an input widget would display."""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def validate(self, value: object) -> str:
        """Return the error message for ``value``, or an empty string if it is fine."""
        raw = self.text(value).strip()
        if self.is_hidden:
            return ""
        if self.required and not raw and self.kind != "boolean":
            return f"{self.label} is required"
        if not raw:
            return ""
        if self.kind == "integer":
            try:
                int(raw)
            except ValueError:
                return f"{self.label} must be a whole number"
        elif self.kind == "number":
            try:
                float(raw)
            except ValueError:
                return f"{self.label} must be a number"
        elif self.kind == "boolean":
            if raw.casefold() not in _TRUE_VALUES | _FALSE_VALUES:
                return f"{self.label} must be yes or no"
        elif self.kind == "autocomplete":
            if self.choices and raw not in self.choices:
                return f"{self.label} must be one of: {', '.join(self.choices)}"
        elif self.kind == "date":
            if not _is_iso_date(raw):
                return f"{self.label} must be a date as YYYY-MM-DD"
        return ""

    def parsed(self, value: object) -> object:
        """Return ``value`` converted to the Python type its kind implies."""
        raw = self.text(value).strip()
        if self.kind == "integer":
            return int(raw) if raw else 0
        if self.kind == "number":
            return float(raw) if raw else 0.0
        if self.kind == "boolean":
            return raw.casefold() in _TRUE_VALUES
        return raw


def _is_iso_date(raw: str) -> bool:
    """True when ``raw`` is a real calendar date written as ``YYYY-MM-DD``."""
    from datetime import date

    parts = raw.split("-")
    if len(parts) != 3:
        return False
    try:
        date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return False
    return True


@dataclass
class Form:
    """A named set of :class:`FormField` values, plus its submit label."""

    name: str
    fields: list[FormField] = field(default_factory=list)
    submit_label: str = "OK"
    cancel_label: str = "Cancel"

    def __post_init__(self) -> None:
        """Reject duplicate field names, which would make values ambiguous."""
        seen: set[str] = set()
        for item in self.fields:
            if item.name in seen:
                raise ValueError(f"duplicate field name: {item.name!r}")
            seen.add(item.name)

    def __iter__(self) -> Iterator[FormField]:
        """Iterate over every field, hidden ones included."""
        return iter(self.fields)

    def field(self, name: str) -> FormField:
        """Return the field called ``name``; unknown names are rejected."""
        for item in self.fields:
            if item.name == name:
                return item
        raise KeyError(name)

    def visible_fields(self) -> list[FormField]:
        """Return the fields the user is shown, in order (hidden ones dropped)."""
        return [item for item in self.fields if not item.is_hidden]

    def defaults(self) -> dict[str, str]:
        """Return the field defaults as the strings a fresh input would hold."""
        return {item.name: item.text(item.default) for item in self.fields}

    def with_defaults(self, **values: object) -> "Form":
        """Return a deep copy of this blueprint with ``values`` applied.

        ADR-007 asks for the create and edit forms to be one definition: the
        rename form is the new-item form with the current name filled in, and
        copying first is what keeps the blueprint itself untouched.
        """
        form = deepcopy(self)
        for item in form.fields:
            if item.name in values:
                item.default = values[item.name]  # type: ignore[assignment]
        return form

    def validate(self, values: Mapping[str, object]) -> dict[str, str]:
        """Return ``{field name: error}`` for ``values``; empty means submittable."""
        errors: dict[str, str] = {}
        for item in self.fields:
            message = item.validate(values.get(item.name, item.default))
            if message:
                errors[item.name] = message
        return errors

    def parsed(self, values: Mapping[str, object]) -> dict[str, object]:
        """Return ``values`` converted field by field into Python types."""
        return {item.name: item.parsed(values.get(item.name, item.default)) for item in self.fields}


#: Blueprint for "create a new file": one required name.
NEW_FILE_FORM = Form(
    name="new_file",
    fields=[FormField(name="name", label="Name", required=True, placeholder="untitled.txt")],
    submit_label="Create",
)

#: Blueprint for "create a new directory": one required name.
NEW_FOLDER_FORM = Form(
    name="new_folder",
    fields=[FormField(name="name", label="Name", required=True, placeholder="untitled folder")],
    submit_label="Create",
)

#: Blueprint for "rename": the same name field, prefilled by ``with_defaults``.
RENAME_FORM = Form(
    name="rename",
    fields=[FormField(name="name", label="New name", required=True)],
    submit_label="Rename",
)
