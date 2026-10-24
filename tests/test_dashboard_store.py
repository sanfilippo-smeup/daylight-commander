"""Unit tests for the dashboard store (DC-024, DC-031).

Every test pins the store root inside ``tmp_path`` (or monkeypatches
``XDG_DATA_HOME``), so nothing is written to the real user data directory.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from daylight.managers.dashboard_store import DashboardStore, default_root
from daylight.models import DashboardArtifact, DashboardBlock, DashboardSpec


def _spec() -> DashboardSpec:
    """The spec used for save/load round-trips."""
    return DashboardSpec(
        schema_version=1,
        title="Files",
        blocks=(DashboardBlock(kind="bar_chart", source="extension_counts", top_n=8),),
    )


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

    artifact = store.save(name="Sales", prompt="chart of sales", spec=_spec(), directory="/dir")

    assert isinstance(artifact, DashboardArtifact)
    assert artifact.name == "Sales"
    assert artifact.prompt == "chart of sales"
    assert artifact.spec == _spec()
    assert artifact.directory == "/dir"
    assert artifact.legacy is False
    assert artifact.script == ""
    assert artifact.id
    assert artifact.created > 0
    assert artifact.modified == artifact.created
    path = tmp_path / f"{artifact.id}.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "Sales"
    assert data["directory"] == "/dir"
    assert data["spec"] == {
        "schema_version": 1,
        "title": "Files",
        "blocks": [
            {
                "kind": "bar_chart",
                "source": "extension_counts",
                "title": "",
                "top_n": 8,
                "color_token": "accent",
            }
        ],
    }
    assert "script" not in data  # the spec file carries no script (DC-031)


def test_list_returns_artifacts_newest_first(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path)
    first = store.save(name="first", prompt="p", spec=_spec(), directory="/d")
    time.sleep(0.011)
    second = store.save(name="second", prompt="p", spec=_spec(), directory="/d")
    time.sleep(0.011)
    third = store.save(name="third", prompt="p", spec=_spec(), directory="/d")

    listed = store.list()

    assert [artifact.id for artifact in listed] == [third.id, second.id, first.id]
    assert listed[0].name == "third"


def test_load_round_trips(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path)
    saved = store.save(name="Round", prompt="the prompt", spec=_spec(), directory="/dir")

    loaded = store.load(saved.id)

    assert loaded == saved
    assert loaded.spec == _spec()
    assert loaded.directory == "/dir"


def test_load_missing_id_raises_key_error(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path)

    with pytest.raises(KeyError):
        store.load("no-such-id")


def test_delete_removes_the_artifact(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path)
    saved = store.save(name="gone", prompt="p", spec=_spec(), directory="/d")

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
    good = store.save(name="good", prompt="p", spec=_spec(), directory="/d")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "wrongshape.json").write_text('{"id": "x"}', encoding="utf-8")

    listed = store.list()

    assert [artifact.id for artifact in listed] == [good.id]
    with pytest.raises(KeyError):
        store.load("broken")


# -- Revision-6 spec files ------------------------------------------------------


def test_a_file_with_an_invalid_spec_is_unreadable_not_legacy(tmp_path: Path) -> None:
    """A spec the validator rejects is corrupt, same as bad JSON (DC-031)."""
    store = DashboardStore(tmp_path)
    good = store.save(name="good", prompt="p", spec=_spec(), directory="/d")
    bad_spec = {
        "schema_version": 1,
        "title": "X",
        "blocks": [{"kind": "nope", "source": "summary"}],  # unknown kind
    }
    (tmp_path / "badspec.json").write_text(
        json.dumps(
            {
                "id": "badspec",
                "name": "bad",
                "prompt": "p",
                "spec": bad_spec,
                "directory": "/d",
                "created": 1.0,
                "modified": 1.0,
            }
        ),
        encoding="utf-8",
    )

    listed = store.list()

    assert [artifact.id for artifact in listed] == [good.id]
    with pytest.raises(KeyError):
        store.load("badspec")


# -- Revision-5 legacy script files ---------------------------------------------


def test_a_revision5_script_file_is_legacy_with_spec_none(tmp_path: Path) -> None:
    """A script-only file loads as legacy and is never executed (DC-031)."""
    store = DashboardStore(tmp_path)
    (tmp_path / "old1.json").write_text(
        json.dumps(
            {
                "id": "old1",
                "name": "Old",
                "prompt": "old prompt",
                "script": "print('hi')",
                "created": 1.0,
                "modified": 2.0,
            }
        ),
        encoding="utf-8",
    )

    listed = store.list()
    artifact = store.load("old1")

    assert [entry.id for entry in listed] == ["old1"]
    assert artifact.legacy is True
    assert artifact.spec is None
    assert artifact.script == "print('hi')"
    assert artifact.name == "Old"
    assert artifact.prompt == "old prompt"
    assert artifact.created == 1.0
    assert artifact.modified == 2.0


def test_save_creates_a_new_revision_each_time(tmp_path: Path) -> None:
    store = DashboardStore(tmp_path)
    first = store.save(name="dash", prompt="p1", spec=_spec(), directory="/d1")
    time.sleep(0.011)
    second = store.save(name="dash", prompt="p2", spec=_spec(), directory="/d2")

    assert first.id != second.id
    assert len(store.list()) == 2
    assert store.load(second.id).prompt == "p2"
    assert store.load(second.id).directory == "/d2"
