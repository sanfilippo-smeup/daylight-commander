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
    "up": "up",
    "down": "down",
    "switch_panel": "ctrl+t",
    "go_up": "backspace",
    "start_filter": "slash",
    "toggle_hidden": "ctrl+h",
    "cycle_sort": "s",
    "reverse_sort": "r",
    "toggle_select": "space",
    "select_all": "ctrl+a",
    "clear_selection": "a",
    "copy_to_other": "f5",
    "move_to_other": "f6",
    "new_directory": "f7",
    "new_file": "f8",
    "rename": "n",
    "delete": "d",
    "delete_permanent": "D",
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
