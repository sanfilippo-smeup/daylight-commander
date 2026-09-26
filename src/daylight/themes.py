"""Theme catalogue: :class:`daylight.models.Theme` -> Textual theme/CSS variables."""

from __future__ import annotations

from textual.design import ColorSystem
from textual.theme import Theme as TextualTheme

from daylight.models import Theme

__all__ = [
    "DEFAULT_THEME",
    "THEMES",
    "get_css_variables",
    "get_theme",
    "to_color_system",
    "to_textual_theme",
]

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

THEMES: dict[str, Theme] = {DEFAULT_THEME.name: DEFAULT_THEME}


def get_theme(name: str) -> Theme:
    """Return the theme registered under ``name`` (default theme as fallback)."""
    return THEMES.get(name, DEFAULT_THEME)


def get_css_variables(theme: Theme) -> dict[str, str]:
    """Return the app-specific ``.tcss`` variables (``$name``) for ``theme``.

    Colors that the Textual color system already derives are not repeated here.
    """
    return {"muted": theme.muted}


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
        dark=True,
        variables=get_css_variables(theme),
    )


def to_color_system(theme: Theme) -> ColorSystem:
    """Convert a :class:`Theme` into the Textual ``ColorSystem`` it implies."""
    return to_textual_theme(theme).to_color_system()
