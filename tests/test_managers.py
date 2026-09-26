"""Unit tests for the managers layer (filesystem access)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from daylight.config import DEFAULT_CONFIG
from daylight.managers import ConfigManager, FileManager

MANAGERS_DIR = Path(__file__).resolve().parents[1] / "src" / "daylight" / "managers"
UI_MODULES = {"textual", "daylight.app", "daylight.themes"}


@pytest.fixture
def populated_dir(tmp_path: Path) -> Path:
    """A temp dir with files, a subdir, a symlink and a dotfile."""
    (tmp_path / "alpha.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("beta!", encoding="utf-8")
    (tmp_path / ".hidden").write_text("secret", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "link.txt").symlink_to(tmp_path / "alpha.txt")
    return tmp_path


def test_list_dir_returns_entries_dirs_first(populated_dir: Path) -> None:
    entries = FileManager().list_dir(populated_dir)
    assert [entry.name for entry in entries] == ["docs", "alpha.txt", "beta.txt", "link.txt"]


def test_list_dir_hides_dotfiles_by_default(populated_dir: Path) -> None:
    entries = FileManager().list_dir(populated_dir)
    names = [entry.name for entry in entries]
    assert ".hidden" not in names
    assert all(entry.is_hidden is False for entry in entries)


def test_list_dir_can_show_hidden(populated_dir: Path) -> None:
    entries = FileManager(show_hidden=True).list_dir(populated_dir)
    names = [entry.name for entry in entries]
    assert ".hidden" in names
    assert [entry.name for entry in entries if entry.is_hidden] == [".hidden"]


def test_list_dir_reports_size_and_symlink(populated_dir: Path) -> None:
    entries = {entry.name: entry for entry in FileManager().list_dir(populated_dir)}
    assert entries["alpha.txt"].size == len("alpha")
    assert entries["docs"].is_dir is True
    assert entries["link.txt"].is_symlink is True


def test_list_dir_accepts_str_path(populated_dir: Path) -> None:
    entries = FileManager().list_dir(str(populated_dir))
    assert {entry.name for entry in entries} == {"alpha.txt", "beta.txt", "docs", "link.txt"}


def test_list_dir_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        FileManager().list_dir(tmp_path / "nope")


def test_stat_returns_file_entry(populated_dir: Path) -> None:
    entry = FileManager().stat(populated_dir / "beta.txt")
    assert entry.name == "beta.txt"
    assert entry.path == populated_dir / "beta.txt"
    assert entry.is_dir is False
    assert entry.size == len("beta!")


def test_stat_dotfile_is_hidden(tmp_path: Path) -> None:
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    assert FileManager().stat(tmp_path / ".hidden").is_hidden is True


def test_stat_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        FileManager().stat(tmp_path / "missing.txt")


def test_config_manager_stub_returns_defaults(tmp_path: Path) -> None:
    assert ConfigManager().load() == DEFAULT_CONFIG
    assert ConfigManager(tmp_path / "config.json").load() == DEFAULT_CONFIG


def _imported_modules(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_managers_do_not_import_ui_modules() -> None:
    for module_path in sorted(MANAGERS_DIR.glob("*.py")):
        imported = _imported_modules(module_path)
        offenders = {name for name in imported if name in UI_MODULES}
        assert not offenders, f"{module_path.name} imports UI modules: {sorted(offenders)}"
