"""Default settings, keybindings and symbols."""

from __future__ import annotations

from daylight.models import AppConfig

__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_KEYBINDINGS",
    "SYMBOLS",
]

#: Action -> key binding. Grows as pages are implemented.
DEFAULT_KEYBINDINGS: dict[str, str] = {
    "quit": "ctrl+q",
    "help": "f1",
    "command_palette": "ctrl+p",
    # Tab navigation (DC-005). Every page is also reachable from the palette and
    # by clicking its tab, so these are the third way in, not the only one.
    "goto_files": "ctrl+1",
    "goto_viewers": "ctrl+2",
    "goto_properties": "ctrl+3",
    "goto_settings": "ctrl+4",
    "goto_stats": "ctrl+5",
    "goto_builder": "ctrl+6",
    "goto_filetypes": "ctrl+7",
    # DC-036: two keys any terminal transmits for walking the tabs in order.
    # Terminal.app never sends ``ctrl+digit`` at all, so ``ctrl+1``…``ctrl+7``
    # above are dead keys there; these cover the same tabs without them and
    # wrap around, while the digits stay available where the terminal sends
    # them.
    "next_page": "ctrl+n",
    "previous_page": "ctrl+b",
    "up": "up",
    "down": "down",
    "switch_panel": "ctrl+t",
    "go_up": "backspace",
    "start_filter": "slash",
    # DC-012: operations live on F-keys and Ctrl/modified keys, never on a
    # printable letter, so type-ahead (DC-007) can claim every letter key.
    "cycle_sort": "ctrl+s",
    "reverse_sort": "ctrl+r",
    "toggle_select": "space",
    "select_all": "ctrl+a",
    "clear_selection": "ctrl+shift+a",
    "copy_to_other": "f5",
    "move_to_other": "f6",
    "new_directory": "f7",
    "new_file": "shift+f7",
    "rename": "shift+f6",
    "delete": "f8",
    "delete_permanent": "shift+f8",
    "view": "f3",
    "properties": "alt+enter",
    "edit": "f4",
    "copy_property": "ctrl+c",
    "next_match": "ctrl+g",
    "search_viewer": "ctrl+f",
    "close_viewer": "escape",
    # External launches (DC-006). ``f9`` opens a URL found in the entry under the
    # cursor with the default browser, always behind a confirmation (FR-402).
    "open_link": "f9",
}

#: Kind -> display symbol.
SYMBOLS: dict[str, str] = {
    "dir": "\u25b8",
    "file": " ",
    "symlink": "\u2192",
    "error": "\u2716",
}

#: ``AppConfig.start_path`` is None: callers resolve the working directory at startup.
DEFAULT_CONFIG: AppConfig = AppConfig()
