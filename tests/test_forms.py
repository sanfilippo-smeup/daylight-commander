"""Declarative form schemas (ADR-007) for the new-file and rename forms.

No app is needed to exercise these: a form is data, so every rule below is a
plain call on a :class:`daylight.forms.Form`.
"""

from __future__ import annotations

import pytest

from daylight.forms import (
    FIELD_KINDS,
    NEW_FILE_FORM,
    NEW_FOLDER_FORM,
    RENAME_FORM,
    Form,
    FormField,
)

# -- the schema itself -------------------------------------------------------


def test_every_kind_adr_007_names_is_supported() -> None:
    assert set(FIELD_KINDS) == {
        "string",
        "number",
        "integer",
        "boolean",
        "autocomplete",
        "date",
        "hidden",
    }


def test_a_field_rejects_a_kind_it_does_not_know() -> None:
    with pytest.raises(ValueError, match="unknown field kind"):
        FormField(name="x", label="X", kind="colour")


def test_a_form_rejects_two_fields_with_the_same_name() -> None:
    with pytest.raises(ValueError, match="duplicate field name"):
        Form(name="f", fields=[FormField(name="a", label="A"), FormField(name="a", label="A2")])


def test_field_lookup_finds_and_rejects_by_name() -> None:
    assert RENAME_FORM.field("name").label == "New name"
    with pytest.raises(KeyError):
        RENAME_FORM.field("nope")


def test_a_form_iterates_and_hides_its_hidden_fields() -> None:
    form = Form(
        name="f",
        fields=[
            FormField(name="shown", label="Shown"),
            FormField(name="carried", label="Carried", kind="hidden", default="42"),
        ],
    )
    assert [item.name for item in form] == ["shown", "carried"]
    assert [item.name for item in form.visible_fields()] == ["shown"]
    assert form.defaults() == {"shown": "", "carried": "42"}


# -- ADR-007: one blueprint, populated defaults -----------------------------


def test_with_defaults_leaves_the_blueprint_alone() -> None:
    rename = RENAME_FORM.with_defaults(name="alpha.txt")
    assert rename.defaults() == {"name": "alpha.txt"}
    assert RENAME_FORM.defaults() == {"name": ""}, "the blueprint must not be mutated"


def test_with_defaults_is_a_deep_copy() -> None:
    rename = RENAME_FORM.with_defaults(name="alpha.txt")
    rename.field("name").choices = ("mutated",)
    assert RENAME_FORM.field("name").choices == ()


def test_the_new_file_and_rename_forms_share_their_field_name() -> None:
    assert NEW_FILE_FORM.field("name").name == RENAME_FORM.field("name").name
    assert NEW_FOLDER_FORM.field("name").name == RENAME_FORM.field("name").name


def test_the_new_item_forms_require_a_name() -> None:
    assert NEW_FILE_FORM.field("name").required is True
    assert NEW_FOLDER_FORM.field("name").required is True


def test_the_new_item_forms_name_their_own_action() -> None:
    assert NEW_FILE_FORM.submit_label == "Create"
    assert NEW_FOLDER_FORM.submit_label == "Create"
    assert RENAME_FORM.submit_label == "Rename"


# -- validation --------------------------------------------------------------


def test_an_empty_required_name_is_an_error() -> None:
    assert NEW_FILE_FORM.validate({"name": ""}) == {"name": "Name is required"}


def test_a_blank_required_name_is_an_error() -> None:
    assert NEW_FILE_FORM.validate({"name": "   "}) == {"name": "Name is required"}


def test_a_filled_name_validates() -> None:
    assert NEW_FILE_FORM.validate({"name": "notes.txt"}) == {}


def test_a_missing_value_falls_back_to_the_default() -> None:
    assert RENAME_FORM.with_defaults(name="alpha").validate({}) == {}


@pytest.mark.parametrize("value", ["1", "-3", "0"])
def test_an_integer_field_accepts_whole_numbers(value: str) -> None:
    form = Form(name="f", fields=[FormField(name="n", label="N", kind="integer")])
    assert form.validate({"n": value}) == {}


@pytest.mark.parametrize("value", ["1.5", "abc", "1,5"])
def test_an_integer_field_rejects_anything_else(value: str) -> None:
    form = Form(name="f", fields=[FormField(name="n", label="N", kind="integer")])
    assert form.validate({"n": value}) == {"n": "N must be a whole number"}


def test_a_number_field_accepts_a_decimal() -> None:
    form = Form(name="f", fields=[FormField(name="n", label="N", kind="number")])
    assert form.validate({"n": "1.5"}) == {}


def test_a_number_field_rejects_words() -> None:
    form = Form(name="f", fields=[FormField(name="n", label="N", kind="number")])
    assert form.validate({"n": "abc"}) == {"n": "N must be a number"}


@pytest.mark.parametrize("value", ["true", "no", "1", "OFF", ""])
def test_a_boolean_field_reads_the_usual_spellings(value: str) -> None:
    form = Form(name="f", fields=[FormField(name="b", label="B", kind="boolean")])
    assert form.validate({"b": value}) == {}


def test_a_boolean_field_rejects_other_words() -> None:
    form = Form(name="f", fields=[FormField(name="b", label="B", kind="boolean")])
    assert form.validate({"b": "maybe"}) == {"b": "B must be yes or no"}


def test_an_autocomplete_field_only_accepts_its_choices() -> None:
    form = Form(
        name="f",
        fields=[FormField(name="c", label="C", kind="autocomplete", choices=("a", "b"))],
    )
    assert form.validate({"c": "a"}) == {}
    assert form.validate({"c": "z"}) == {"c": "C must be one of: a, b"}


def test_a_date_field_only_accepts_a_real_iso_date() -> None:
    form = Form(name="f", fields=[FormField(name="d", label="D", kind="date")])
    assert form.validate({"d": "2026-03-04"}) == {}
    assert form.validate({"d": "2026-13-04"}) == {"d": "D must be a date as YYYY-MM-DD"}
    assert form.validate({"d": "04/03/2026"}) == {"d": "D must be a date as YYYY-MM-DD"}


def test_a_hidden_field_is_never_invalidated() -> None:
    form = Form(
        name="f", fields=[FormField(name="h", label="H", kind="hidden", required=True, default="x")]
    )
    assert form.validate({}) == {}


# -- conversion --------------------------------------------------------------


def test_parsed_converts_each_field_to_its_python_type() -> None:
    form = Form(
        name="f",
        fields=[
            FormField(name="n", label="N", kind="integer", default="7"),
            FormField(name="r", label="R", kind="number", default="1.5"),
            FormField(name="b", label="B", kind="boolean", default="yes"),
            FormField(name="s", label="S", default="text"),
            FormField(name="h", label="H", kind="hidden", default="carried"),
        ],
    )
    assert form.parsed({}) == {
        "n": 7,
        "r": 1.5,
        "b": True,
        "s": "text",
        "h": "carried",
    }
    assert form.parsed({"n": "9", "s": "typed"})["n"] == 9
    assert form.parsed({"n": "9", "s": "typed"})["s"] == "typed"


def test_text_renders_a_boolean_the_way_an_input_shows_it() -> None:
    assert FormField(name="b", label="B", kind="boolean").text(True) == "true"
    assert FormField(name="b", label="B", kind="boolean").text(False) == "false"
    assert FormField(name="n", label="N").text(7) == "7"
