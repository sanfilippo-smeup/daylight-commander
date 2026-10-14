"""The persistent app shell: header (title, path, page tabs) and the hint bar.

The header and the hint bar stay on screen for the whole session and for every
page (FR-501, FR-502); the ``.content`` container between them is the only part
that changes, which is what lets a tab round trip keep the page's state.

Every widget here is unstyled Python: colors come from the ``$name`` variables
:func:`daylight.themes.get_css_variables` publishes, in ``styles/shell.tcss``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.keys import format_key as format_key_character
from textual.widgets import Static, Tab, Tabs

__all__ = ["KeyHintBar", "NoticeLine", "ShellHeader", "format_hints", "format_key"]

#: What separates two hints in the bar (DC-019): a middle dot, distinct from
#: the ``│`` field separator the panel headers use.
HINT_SEPARATOR = " · "


def format_key(key: str) -> str:
    """``ctrl+pageup`` -> ``Ctrl+pgup``, ``slash`` -> ``/``: a key as a hint.

    Modifiers are spelled out and the key itself is named the way Textual names
    it, so the bar and the palette say ``Ctrl+A``, not ``ctrl+a``. Function
    keys read the way the keyboard labels them (``F5``, not ``f5``).
    """
    *modifiers, character = key.split("+")
    shown = format_key_character(character)
    if len(shown) == 1 and shown.isalpha():
        shown = shown.upper()
    elif shown[:1] in ("f", "F") and shown[1:].isdigit():
        shown = shown.capitalize()
    return "+".join([*(part.capitalize() for part in modifiers), shown])


def format_hints(hints: Sequence[tuple[str, str]]) -> str:
    """``[("ctrl+p", "Palette")]`` -> ``"Ctrl+P Palette"``, joined into one line."""
    return HINT_SEPARATOR.join(
        f"{format_key(key)} {description}" for key, description in hints
    )


class ShellHeader(Vertical):
    """App title, the path being browsed, and one tab per page."""

    DEFAULT_CLASSES = "shell-header"

    def __init__(self, title: str, pages: dict[str, str], active: str = "") -> None:
        self._title = Static(title, id="daylight-title")
        self._path = Static("", id="daylight-path")
        #: Tab id is the page name, so ``Tabs.TabActivated`` names a page directly.
        self.tabs = Tabs(
            *(Tab(label, id=name) for name, label in pages.items()),
            active=active or None,
            id="daylight-tabs",
        )
        super().__init__()

    @property
    def title_widget(self) -> Static:
        """The static that holds the app title."""
        return self._title

    @property
    def path_widget(self) -> Static:
        """The static that holds the current path."""
        return self._path

    def compose(self) -> ComposeResult:
        """The title line and the tab strip."""
        with Horizontal(classes="shell-titlebar"):
            yield self._title
            yield self._path
        yield self.tabs

    def set_path(self, path: str) -> None:
        """Show ``path`` in the header (AC1)."""
        self._path.update(f" {path} " if path else "")

    def path_text(self) -> str:
        """The header path as plain text, without the padding spaces."""
        return str(self._path.content).strip()


class NoticeLine(Static):
    """One line of explanation above a page's content, hidden while it is blank.

    A page that has something to show says so here; a page with real content says
    nothing, and a blank row between the header and the content would read as a
    rendering mistake.
    """

    DEFAULT_CLASSES = "view-notice"

    def explain(self, text: str) -> None:
        """Show ``text``, or take the line off screen when there is nothing to say."""
        self.display = bool(text.strip())
        self.update(text)


class KeyHintBar(Static):
    """The footer: the keybindings that are active in the current view (AC1).

    The bar is told what to show rather than working it out itself: which
    bindings are active depends on the page, and the app is what knows the page.
    """

    def __init__(self) -> None:
        self.hints: list[tuple[str, str]] = []
        super().__init__("", id="daylight-hints")

    def show_hints(self, hints: Iterable[tuple[str, str]]) -> None:
        """Render ``hints`` -- the ``(key, description)`` pairs of the current view."""
        self.hints = list(hints)
        self.update(format_hints(self.hints))

    def hint_text(self) -> str:
        """The rendered bar as plain text."""
        return format_hints(self.hints)
