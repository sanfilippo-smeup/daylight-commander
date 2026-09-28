"""Unit tests for ConfigManager persistence (AC6: hidden toggle survives)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daylight.app import DaylightApp
from daylight.config import DEFAULT_CONFIG
from daylight.managers.config_manager import ConfigManager, default_config_path
from daylight.models import AppConfig


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """A config file path inside a temp dir that does not exist yet."""
    return tmp_path / "nested" / "config.json"


def test_load_returns_defaults_when_the_file_is_missing(config_path: Path) -> None:
    assert ConfigManager(config_path).load() == DEFAULT_CONFIG


def test_load_returns_defaults_for_a_corrupt_file(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{not json", encoding="utf-8")
    assert ConfigManager(config_path).load() == DEFAULT_CONFIG


def test_load_returns_defaults_when_the_file_is_a_list(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert ConfigManager(config_path).load() == DEFAULT_CONFIG


def test_save_then_load_round_trips_the_hidden_toggle(config_path: Path) -> None:
    manager = ConfigManager(config_path)
    manager.save(AppConfig(show_hidden=True))
    assert manager.load().show_hidden is True


def test_a_fresh_manager_restores_what_an_earlier_run_persisted(config_path: Path) -> None:
    """AC6 is read-back, not just write: a new manager stands in for the next run.

    Nothing is carried over in memory from the manager that saved, so this can
    only pass if the value really is on disk and really is parsed back out.
    """
    ConfigManager(config_path).save(AppConfig(show_hidden=True, sort_key="time"))

    restored = ConfigManager(config_path).load()
    assert restored.show_hidden is True
    assert restored.sort_key == "time"


def test_the_restored_preference_is_the_one_the_manager_lists_with(
    config_path: Path, tmp_path: Path
) -> None:
    """A restored ``show_hidden`` must reach the filesystem access, not stop at config."""
    (tmp_path / "visible.txt").write_text("v", encoding="utf-8")
    (tmp_path / ".secret").write_text("s", encoding="utf-8")
    ConfigManager(config_path).save(AppConfig(show_hidden=True))

    app = DaylightApp(config_path=config_path)
    listed = {entry.name for entry in app.file_manager.iter_entries(tmp_path)}
    assert app.app_config.show_hidden is True
    assert app.file_manager.show_hidden is True
    assert ".secret" in listed


def test_save_creates_the_parent_directory(config_path: Path) -> None:
    ConfigManager(config_path).save(DEFAULT_CONFIG)
    assert config_path.is_file()


def test_update_persists_and_returns_the_new_config(config_path: Path) -> None:
    manager = ConfigManager(config_path)
    updated = manager.update(show_hidden=True, sort_key="size")
    assert updated.show_hidden is True
    assert updated.sort_key == "size"
    assert manager.load().show_hidden is True
    assert manager.load().sort_key == "size"


def test_update_only_changes_the_named_fields(config_path: Path) -> None:
    manager = ConfigManager(config_path)
    manager.update(show_hidden=True)
    loaded = manager.load()
    assert loaded.theme == DEFAULT_CONFIG.theme
    assert loaded.sort_key == DEFAULT_CONFIG.sort_key


def test_save_writes_readable_json(config_path: Path) -> None:
    ConfigManager(config_path).save(AppConfig(show_hidden=True, sort_key="time"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["show_hidden"] is True
    assert payload["sort_key"] == "time"


def test_start_path_round_trips_as_a_path(config_path: Path) -> None:
    manager = ConfigManager(config_path)
    manager.update(start_path=Path("/tmp"))
    assert manager.load().start_path == Path("/tmp")


def test_load_ignores_values_of_the_wrong_type(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"show_hidden": "yes", "sort_key": 7, "sort_reverse": "no"}),
        encoding="utf-8",
    )
    loaded = ConfigManager(config_path).load()
    assert loaded.show_hidden == DEFAULT_CONFIG.show_hidden
    assert loaded.sort_key == DEFAULT_CONFIG.sort_key
    assert loaded.sort_reverse == DEFAULT_CONFIG.sort_reverse


def test_default_path_is_under_the_daylight_config_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")
    assert default_config_path() == Path("/xdg/daylight/config.json")


def test_default_path_falls_back_to_the_home_config_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert default_config_path() == Path.home() / ".config" / "daylight" / "config.json"


def test_manager_without_a_path_uses_the_default_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")
    assert ConfigManager().path == Path("/xdg/daylight/config.json")


# -- the app reads the persisted config back at startup (AC6) ---------------


def test_app_restores_the_persisted_hidden_toggle(config_path: Path) -> None:
    """A saved preference must come back on the next start, not just be written."""
    ConfigManager(config_path).save(AppConfig(show_hidden=True))
    app = DaylightApp(config_path=config_path)
    assert app.app_config.show_hidden is True
    assert app.file_manager.show_hidden is True


def test_app_starts_from_the_defaults_when_nothing_was_saved(config_path: Path) -> None:
    app = DaylightApp(config_path=config_path)
    assert app.app_config == DEFAULT_CONFIG
    assert app.file_manager.show_hidden is False


def test_app_survives_a_corrupt_config_file(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{not json", encoding="utf-8")
    assert DaylightApp(config_path=config_path).app_config == DEFAULT_CONFIG


def test_an_explicit_config_wins_over_the_persisted_one(config_path: Path) -> None:
    ConfigManager(config_path).save(AppConfig(show_hidden=True))
    app = DaylightApp(AppConfig(show_hidden=False), config_path=config_path)
    assert app.app_config.show_hidden is False
    assert app.file_manager.show_hidden is False


def test_app_restores_the_persisted_start_path(config_path: Path) -> None:
    ConfigManager(config_path).save(AppConfig(start_path=Path("/tmp")))
    assert DaylightApp(config_path=config_path).start_path == Path("/tmp")


def test_app_restores_every_persisted_field(config_path: Path) -> None:
    saved = AppConfig(start_path=Path("/tmp"), show_hidden=True, sort_key="size")
    ConfigManager(config_path).save(saved)
    assert DaylightApp(config_path=config_path).app_config == saved
