"""Unit tests for the pure data models."""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path
from typing import Mapping, get_type_hints

from daylight.models import AppConfig, FileEntry, Theme


def test_file_entry_is_dataclass_with_type_hints() -> None:
    assert dataclasses.is_dataclass(FileEntry)
    hints = get_type_hints(FileEntry)
    assert hints["name"] is str
    assert hints["path"] is Path
    assert hints["is_dir"] is bool
    assert hints["size"] is int
    assert hints["modified"] is float


def test_file_entry_defaults_and_equality() -> None:
    minimal = FileEntry(name="notes.txt", path=Path("/tmp/notes.txt"), is_dir=False)
    assert minimal.is_symlink is False
    assert minimal.size == 0
    assert minimal.modified == 0.0
    assert minimal.is_hidden is False
    assert minimal == FileEntry(name="notes.txt", path=Path("/tmp/notes.txt"), is_dir=False)


def test_file_entry_optional_fields_are_typed() -> None:
    optional = {
        name: param.default
        for name, param in inspect.signature(FileEntry).parameters.items()
        if param.default is not inspect.Parameter.empty
    }
    assert optional == {
        "is_symlink": False,
        "size": 0,
        "modified": 0.0,
        "is_hidden": False,
    }


def _make_theme(**overrides: str) -> Theme:
    values: dict[str, str] = {
        "name": "t",
        "background": "#000000",
        "surface": "#111111",
        "panel": "#222222",
        "foreground": "#ffffff",
        "muted": "#888888",
        "accent": "#f0b429",
        "secondary": "#4a90d9",
        "success": "#4caf50",
        "warning": "#ffb300",
        "error": "#ef5350",
    }
    values.update(overrides)
    return Theme(**values)


def test_theme_is_dataclass_with_typed_colors() -> None:
    assert dataclasses.is_dataclass(Theme)
    hints = get_type_hints(Theme)
    assert hints["name"] is str
    color_fields = {name for name, hint in hints.items() if hint is str and name != "name"}
    assert {"background", "foreground", "accent", "muted", "error"} <= color_fields


def test_theme_values_round_trip() -> None:
    theme = _make_theme(name="custom")
    assert theme.name == "custom"
    assert theme.background == "#000000"
    assert theme == _make_theme(name="custom")


def test_app_config_defaults() -> None:
    assert dataclasses.is_dataclass(AppConfig)
    config = AppConfig()
    assert config.theme == "daylight-dark"
    assert config.start_path is None
    assert config.show_hidden is False
    assert config.sort_key == "name"
    assert config.sort_reverse is False
    assert dict(config.keybindings) == {}


def test_app_config_keybindings_are_not_shared() -> None:
    assert AppConfig().keybindings is not AppConfig().keybindings


def test_app_config_is_typed() -> None:
    hints = get_type_hints(AppConfig)
    assert hints["theme"] is str
    assert hints["start_path"] == Path | None
    assert hints["show_hidden"] is bool
    assert hints["keybindings"] == Mapping[str, str]
