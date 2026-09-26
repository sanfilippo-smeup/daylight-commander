"""Configuration access. Stub for DC-001: defaults only, no persistence yet."""

from __future__ import annotations

import os
from pathlib import Path

from daylight.config import DEFAULT_CONFIG
from daylight.models import AppConfig

__all__ = ["ConfigManager"]


class ConfigManager:
    """Loads the application configuration."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else None

    def load(self) -> AppConfig:
        """Return the active configuration.

        Stub for DC-001: always returns the defaults; persistence lands later.
        """
        return DEFAULT_CONFIG
