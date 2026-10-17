"""Unit tests for the dashboard store (DC-024).

Every test pins the store root inside ``tmp_path`` (or monkeypatches
``XDG_DATA_HOME``), so nothing is written to the real user data directory.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from daylight.managers.dashboard_store import DashboardStore, default_root
from daylight.models import DashboardArtifact

# -- default root -------------------------------------------------------------


def test_default_root_uses_xdg_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert default_root() == tmp_path / "daylight" / "dashboards"
    assert DashboardStore().root == tmp_path / "daylight" / "dashboards"


def test_default_root_falls_back_to_local_share(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_root() == tmp_path / ".local" / "share" / "daylight" / "dashboards"


def test_default_root_ignores_empty_xdg_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", "   ")

    assert default_root() == tmp_path / ".local" / "share" / "daylight" / "dashboards"


def test_explicit_root_is_accepted(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path / "custom")

    assert store.root == tmp_path / "custom"


# -- save / list / load / delete ----------------------------------------------


def test_save_returns_artifact_and_writes_json(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path)

    artifact = store.save(name="Sales", prompt="chart of sales", script="print('hi')")

    assert isinstance(artifact, DashboardArtifact)
    assert artifact.name == "Sales"
    assert artifact.prompt == "chart of sales"
    assert artifact.script == "print('hi')"
    assert artifact.id
    assert artifact.created > 0
    assert artifact.modified == artifact.created
    path = tmp_path / f"{artifact.id}.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "Sales"
    assert data["script"] == "print('hi')"


def test_list_returns_artifacts_newest_first(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path)
    first = store.save(name="first", prompt="p", script="s")
    time.sleep(0.011)
    second = store.save(name="second", prompt="p", script="s")
    time.sleep(0.011)
    third = store.save(name="third", prompt="p", script="s")

    listed = store.list()

    assert [artifact.id for artifact in listed] == [third.id, second.id, first.id]
    assert listed[0].name == "third"


def test_load_round_trips(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path)
    saved = store.save(name="Round", prompt="the prompt", script="print(1)")

    loaded = store.load(saved.id)

    assert loaded == saved


def test_load_missing_id_raises_key_error(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path)

    with pytest.raises(KeyError):
        store.load("no-such-id")


def test_delete_removes_the_artifact(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path)
    saved = store.save(name="gone", prompt="p", script="s")

    store.delete(saved.id)

    assert store.list() == []
    assert not (tmp_path / f"{saved.id}.json").exists()
    with pytest.raises(KeyError):
        store.load(saved.id)


def test_delete_missing_id_raises_key_error(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path)

    with pytest.raises(KeyError):
        store.delete("no-such-id")


def test_list_on_missing_root_is_empty(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path / "absent")

    assert store.list() == []


def test_corrupt_files_are_skipped_by_list_and_load(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path)
    good = store.save(name="good", prompt="p", script="s")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "wrongshape.json").write_text('{"id": "x"}', encoding="utf-8")

    listed = store.list()

    assert [artifact.id for artifact in listed] == [good.id]
    with pytest.raises(KeyError):
        store.load("broken")


def test_save_creates_a_new_revision_each_time(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path)
    first = store.save(name="dash", prompt="p1", script="s1")
    time.sleep(0.011)
    second = store.save(name="dash", prompt="p2", script="s2")

    assert first.id != second.id
    assert len(store.list()) == 2
    assert store.load(second.id).prompt == "p2"
