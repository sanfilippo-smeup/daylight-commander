"""Configuration access: JSON-backed load/save for user-visible settings."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from daylight.config import DEFAULT_CONFIG
from daylight.models import AppConfig

__all__ = ["ConfigManager", "default_config_path"]

#: Keys persisted to disk, in a stable order.
_FIELDS: tuple[str, ...] = ("theme", "start_path", "show_hidden", "sort_key", "sort_reverse")


def default_config_path() -> Path:
    """Return the per-user configuration file path (``$XDG_CONFIG_HOME`` aware)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "daylight" / "config.json"


class ConfigManager:
    """Loads and persists the application configuration."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else default_config_path()

    def load(self) -> AppConfig:
        """Return the active configuration.

        Falls back to :data:`daylight.config.DEFAULT_CONFIG` when the file is
        missing, unreadable or malformed, so a broken config never blocks start.
        """
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return DEFAULT_CONFIG
        if not isinstance(raw, dict):
            return DEFAULT_CONFIG
        return self._from_mapping(raw)

    def save(self, config: AppConfig) -> AppConfig:
        """Write ``config`` to disk and return it unchanged."""
        payload: dict[str, object] = {}
        for field in _FIELDS:
            value = getattr(config, field)
            payload[field] = str(value) if isinstance(value, Path) else value
        payload["keybindings"] = dict(config.keybindings)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return config

    def update(self, **changes: object) -> AppConfig:
        """Apply ``changes`` to the loaded config, persist it and return it."""
        return self.save(replace(self.load(), **changes))

    @staticmethod
    def _from_mapping(raw: dict[str, object]) -> AppConfig:
        """Build an :class:`AppConfig` from decoded JSON, ignoring bad values."""
        base = DEFAULT_CONFIG
        theme = raw.get("theme")
        if not isinstance(theme, str) or not theme:
            theme = base.theme
        start_path = raw.get("start_path")
        if not isinstance(start_path, str) or not start_path:
            start_path = None
        show_hidden = raw.get("show_hidden")
        if not isinstance(show_hidden, bool):
            show_hidden = base.show_hidden
        sort_key = raw.get("sort_key")
        if not isinstance(sort_key, str) or not sort_key:
            sort_key = base.sort_key
        sort_reverse = raw.get("sort_reverse")
        if not isinstance(sort_reverse, bool):
            sort_reverse = base.sort_reverse
        keybindings = raw.get("keybindings")
        return AppConfig(
            theme=theme,
            start_path=Path(start_path) if start_path else None,
            show_hidden=show_hidden,
            sort_key=sort_key,
            sort_reverse=sort_reverse,
            keybindings=keybindings if isinstance(keybindings, dict) else dict(base.keybindings),
        )
