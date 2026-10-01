"""Unit tests for the theme catalogue and the tokens it publishes (DC-005).

Every stylesheet reads colors through these variables (ADR-004), so this file is
also what keeps a color literal from creeping back into the UI: a token that is
not in :data:`daylight.themes.THEME_TOKENS` is a token nothing may reference.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from textual.theme import Theme as TextualTheme

from daylight import themes
from daylight.models import Theme

#: ``color: #ff0000`` and friends: a literal color in a stylesheet.
LITERAL_COLOR = re.compile(r":\s*(#[0-9a-fA-F]{3,8}|rgb\(|hsl\()")

#: The stylesheets every color in the UI is allowed to come from.
STYLESHEETS = Path(themes.__file__).parent / "styles"


def test_the_catalogue_offers_more_than_one_theme() -> None:
    """AC4: a theme to switch *to* has to exist first."""
    assert len(themes.theme_names()) >= 2
    assert len(set(themes.theme_names())) == len(themes.theme_names())


def test_the_default_theme_is_the_first_one() -> None:
    assert themes.theme_names()[0] == themes.DEFAULT_THEME.name


def test_a_light_and_a_dark_theme_are_available() -> None:
    assert any(not theme.dark for theme in themes.THEMES.values())
    assert any(theme.dark for theme in themes.THEMES.values())


def test_every_theme_is_registered_under_its_own_name() -> None:
    for name, theme in themes.THEMES.items():
        assert theme.name == name


def test_get_theme_returns_the_named_theme() -> None:
    assert themes.get_theme("daylight-light") is themes.THEMES["daylight-light"]


def test_get_theme_falls_back_to_the_default() -> None:
    assert themes.get_theme("no-such-theme") is themes.DEFAULT_THEME


def test_every_theme_names_a_colour_for_every_field() -> None:
    for theme in themes.THEMES.values():
        for field in ("background", "surface", "panel", "foreground", "muted", "accent"):
            value = getattr(theme, field)
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"{theme.name}.{field}={value}"


# -- the CSS variables (ADR-004) ------------------------------------------


@pytest.mark.parametrize("token", themes.THEME_TOKENS)
def test_the_adr_tokens_are_published(token: str) -> None:
    assert themes.get_css_variables(themes.DEFAULT_THEME)[token]


def test_the_published_tokens_are_the_theme_colours() -> None:
    theme = themes.THEMES["daylight-light"]
    variables = themes.get_css_variables(theme)
    assert variables["background"] == theme.background
    assert variables["surface"] == theme.surface
    assert variables["panel"] == theme.panel
    assert variables["accent"] == theme.accent
    assert variables["muted"] == theme.muted


def test_only_the_declared_tokens_are_published() -> None:
    assert set(themes.get_css_variables(themes.DEFAULT_THEME)) == set(themes.THEME_TOKENS)


# -- the stylesheets (ADR-004) ------------------------------------------------


def test_no_stylesheet_carries_a_literal_color() -> None:
    """A color in a ``.tcss`` file would be a color the theme switch cannot reach."""
    sheets = sorted(STYLESHEETS.glob("*.tcss"))
    assert sheets, f"no stylesheets found under {STYLESHEETS}"
    for sheet in sheets:
        literals = LITERAL_COLOR.findall(sheet.read_text(encoding="utf-8"))
        assert not literals, f"{sheet.name} hardcodes {literals}"


# -- the Textual conversions ------------------------------------------------


def test_to_textual_theme_carries_the_palette() -> None:
    theme = themes.THEMES["daylight-contrast"]
    converted = themes.to_textual_theme(theme)
    assert isinstance(converted, TextualTheme)
    assert converted.name == theme.name
    assert converted.accent == theme.accent
    assert converted.background == theme.background
    assert converted.panel == theme.panel
    assert converted.foreground == theme.foreground


def test_a_light_theme_says_it_is_light() -> None:
    """A light palette on Textual's dark design system renders the wrong widgets."""
    assert themes.to_textual_theme(themes.THEMES["daylight-light"]).dark is False
    assert themes.to_textual_theme(themes.DEFAULT_THEME).dark is True


def test_to_textual_theme_keeps_the_app_tokens() -> None:
    theme = themes.THEMES["daylight-light"]
    assert themes.to_textual_theme(theme).variables == themes.get_css_variables(theme)


def test_to_color_system_produces_the_generated_variables() -> None:
    theme = themes.THEMES["daylight-light"]
    generated = themes.to_color_system(theme).generate()
    assert generated["background"].lower() == theme.background.lower()
    assert generated["accent"].lower() == theme.accent.lower()
    assert generated["panel"].lower() == theme.panel.lower()


def test_to_color_system_agrees_with_the_textual_theme() -> None:
    """The two conversions must not drift: they are the same theme twice."""
    for theme in themes.THEMES.values():
        assert themes.to_color_system(theme).generate() == (
            themes.to_textual_theme(theme).to_color_system().generate()
        )


# -- a theme is a value, not a registry lookup ------------------------------


def test_a_theme_can_be_built_without_a_name_in_the_catalogue() -> None:
    """The model is what a palette is; the catalogue is only a set of them."""
    extra = Theme(
        name="daylight-extra",
        background="#000000",
        surface="#111111",
        panel="#222222",
        foreground="#eeeeee",
        muted="#999999",
        accent="#ff00ff",
        secondary="#00ffff",
        success="#00ff00",
        warning="#ffff00",
        error="#ff0000",
    )
    assert themes.get_theme("daylight-extra") is themes.DEFAULT_THEME
    assert themes.to_color_system(extra).generate()["accent"].lower() == "#ff00ff"
