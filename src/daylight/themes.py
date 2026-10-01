"""Theme catalogue: :class:`daylight.models.Theme` -> Textual theme/CSS variables.

The only place in the project that names a color. Every ``.tcss`` file reads
these values through the variables :func:`get_css_variables` exposes, so adding
a theme is adding a palette here and nothing else (ADR-004).
"""

from __future__ import annotations

from textual.design import ColorSystem
from textual.theme import Theme as TextualTheme

from daylight.models import Theme

__all__ = [
    "DEFAULT_THEME",
    "THEMES",
    "THEME_TOKENS",
    "get_css_variables",
    "get_theme",
    "theme_names",
    "to_color_system",
    "to_textual_theme",
]

#: The tokens every stylesheet is allowed to reference (ADR-004).
THEME_TOKENS: tuple[str, ...] = ("background", "surface", "panel", "accent", "muted")

DEFAULT_THEME: Theme = Theme(
    name="daylight-dark",
    background="#101418",
    surface="#181d23",
    panel="#1f252c",
    foreground="#e6e6e6",
    muted="#8a939e",
    accent="#f0b429",
    secondary="#4a90d9",
    success="#4caf50",
    warning="#ffb300",
    error="#ef5350",
)

#: The same tokens on a light background; ``dark=False`` is what makes Textual
#: derive light widget variables for it.
LIGHT_THEME: Theme = Theme(
    name="daylight-light",
    background="#f6f7f9",
    surface="#ffffff",
    panel="#e9edf2",
    foreground="#1b2027",
    muted="#5a6472",
    accent="#8a5a00",
    secondary="#1f5fa8",
    success="#2f7d32",
    warning="#8a5a00",
    error="#b3261e",
    dark=False,
)

#: Maximum contrast on black, for terminals that wash dark grays out.
CONTRAST_THEME: Theme = Theme(
    name="daylight-contrast",
    background="#000000",
    surface="#0d0d0d",
    panel="#1a1a1a",
    foreground="#ffffff",
    muted="#bdbdbd",
    accent="#ffd75f",
    secondary="#7fbcff",
    success="#5fd75f",
    warning="#ffb300",
    error="#ff5f5f",
)

#: The catalogue, in switch order. The first entry is the default.
THEMES: dict[str, Theme] = {
    theme.name: theme for theme in (DEFAULT_THEME, LIGHT_THEME, CONTRAST_THEME)
}


def get_theme(name: str) -> Theme:
    """Return the theme registered under ``name`` (default theme as fallback)."""
    return THEMES.get(name, DEFAULT_THEME)


def theme_names() -> tuple[str, ...]:
    """Every theme name, in catalogue order (what the settings page lists)."""
    return tuple(THEMES)


def get_css_variables(theme: Theme) -> dict[str, str]:
    """Return the app's ``.tcss`` variables (``$name``) for ``theme``.

    The four tokens ADR-004 requires (``$accent``, ``$surface``, ``$panel``,
    ``$background``) plus ``$muted``, which the color system does not derive.
    """
    return {
        "background": theme.background,
        "surface": theme.surface,
        "panel": theme.panel,
        "accent": theme.accent,
        "muted": theme.muted,
    }


def to_textual_theme(theme: Theme) -> TextualTheme:
    """Convert a :class:`Theme` into a Textual theme ready for ``App.register_theme``."""
    return TextualTheme(
        name=theme.name,
        primary=theme.accent,
        secondary=theme.secondary,
        accent=theme.accent,
        foreground=theme.foreground,
        background=theme.background,
        surface=theme.surface,
        panel=theme.panel,
        success=theme.success,
        warning=theme.warning,
        error=theme.error,
        dark=theme.dark,
        variables=get_css_variables(theme),
    )


def to_color_system(theme: Theme) -> ColorSystem:
    """Convert a :class:`Theme` into the Textual ``ColorSystem`` it implies."""
    return to_textual_theme(theme).to_color_system()
