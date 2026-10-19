"""Per-user persistence for saved AI dashboards (DC-024/DC-031, FR-806, ADR-016).

A dashboard is one JSON file under the user's data directory
(``$XDG_DATA_HOME/daylight/dashboards``, falling back to
``~/.local/share/daylight/dashboards``), holding the name, the prompt that
produced it, the validated dashboard spec and the directory it was built for,
plus timestamps. Revision-5 files hold a generated *script* instead; those
stay readable -- they come back as ``legacy=True`` with ``spec=None`` and are
never rendered or executed (ADR-016). A file whose spec fails
:func:`parse_spec` validation is unreadable, exactly like corrupt JSON.
:class:`DashboardStore` writes, lists (newest first), loads and deletes those
files; an explicit ``root`` overrides the default location so tests stay
inside ``tmp_path``.

Like every manager, this module never imports UI code (ADR-003, FR-808).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from time import time
from typing import Any

from daylight.managers.dashboard_spec import DashboardSpecError, parse_spec
from daylight.models import DashboardArtifact, DashboardSpec

__all__ = ["DashboardStore", "default_root"]

logger = logging.getLogger("daylight.managers.dashboard_store")

#: Subdirectory of the user data home that holds the saved dashboards.
_STORE_DIRECTORY = Path("daylight") / "dashboards"


def default_root() -> Path:
    """The per-user dashboard directory (AC8).

    ``$XDG_DATA_HOME/daylight/dashboards`` when ``XDG_DATA_HOME`` is set to a
    non-empty value, else ``~/.local/share/daylight/dashboards``.
    """
    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    if data_home:
        return Path(data_home) / _STORE_DIRECTORY
    return Path.home() / ".local" / "share" / _STORE_DIRECTORY


class DashboardStore:
    """Save, list, load and delete dashboard artifacts (AC8).

    Every :meth:`save` writes a new artifact with a fresh id (a re-save is a
    new revision, never an in-place edit). :meth:`list` returns artifacts
    newest first. A missing id raises :class:`KeyError` from :meth:`load` and
    :meth:`delete`; a file that no longer parses is treated as absent.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_root()

    def save(
        self, *, name: str, prompt: str, spec: DashboardSpec, directory: str
    ) -> DashboardArtifact:
        """Write one JSON artifact carrying the spec and its directory (AC6)."""
        self.root.mkdir(parents=True, exist_ok=True)
        now = time()
        artifact = DashboardArtifact(
            id=uuid.uuid4().hex,
            name=name,
            prompt=prompt,
            spec=spec,
            directory=directory,
            created=now,
            modified=now,
        )
        path = self._path(artifact.id)
        path.write_text(_dumps(artifact), encoding="utf-8")
        return artifact

    def list(self) -> list[DashboardArtifact]:
        """Return every readable artifact, newest first (AC8)."""
        if not self.root.is_dir():
            return []
        artifacts: list[DashboardArtifact] = []
        for path in sorted(self.root.glob("*.json")):
            artifact = _read_artifact(path)
            if artifact is not None:
                artifacts.append(artifact)
        artifacts.sort(key=lambda artifact: artifact.modified, reverse=True)
        return artifacts

    def load(self, dashboard_id: str) -> DashboardArtifact:
        """Return the artifact stored under ``dashboard_id`` (AC8).

        Raises :class:`KeyError` when the id is unknown or its file is
        unreadable.
        """
        artifact = _read_artifact(self._path(dashboard_id))
        if artifact is None:
            raise KeyError(dashboard_id)
        return artifact

    def delete(self, dashboard_id: str) -> None:
        """Remove the artifact stored under ``dashboard_id`` (AC8).

        Raises :class:`KeyError` when the id is unknown.
        """
        path = self._path(dashboard_id)
        if not path.is_file():
            raise KeyError(dashboard_id)
        path.unlink()

    def _path(self, dashboard_id: str) -> Path:
        return self.root / f"{dashboard_id}.json"


def _dumps(artifact: DashboardArtifact) -> str:
    """Serialize one artifact as pretty, human-inspectable JSON."""
    data: dict[str, Any] = {
        "id": artifact.id,
        "name": artifact.name,
        "prompt": artifact.prompt,
        "spec": _spec_data(artifact.spec),
        "directory": artifact.directory,
        "created": artifact.created,
        "modified": artifact.modified,
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _spec_data(spec: DashboardSpec) -> dict[str, Any]:
    """The JSON shape of one spec: exactly what :func:`parse_spec` re-accepts."""
    return {
        "schema_version": spec.schema_version,
        "title": spec.title,
        "blocks": [
            {
                "kind": block.kind,
                "source": block.source,
                "title": block.title,
                "top_n": block.top_n,
                "color_token": block.color_token,
            }
            for block in spec.blocks
        ],
    }


def _read_artifact(path: Path) -> DashboardArtifact | None:
    """Parse one artifact file; ``None`` when it is unreadable (AC7).

    Three outcomes: a Revision-6 spec file (the spec must validate), a
    Revision-5 script-only file (``legacy=True``, ``spec=None``) and anything
    else -- corrupt JSON, a wrong shape, or a spec that fails
    :func:`parse_spec` -- which is unreadable and treated as absent.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        common: dict[str, Any] = {
            "id": str(data["id"]),
            "name": str(data["name"]),
            "prompt": str(data["prompt"]),
            "created": float(data["created"]),
            "modified": float(data["modified"]),
        }
    except (KeyError, TypeError, ValueError):
        logger.debug("skipping malformed dashboard file: %s", path, exc_info=True)
        return None
    if "spec" in data:
        try:
            spec = parse_spec(data["spec"])
        except DashboardSpecError:
            # A spec that fails validation is unreadable, not legacy (AC7).
            logger.debug("skipping dashboard file with an invalid spec: %s", path, exc_info=True)
            return None
        return DashboardArtifact(
            spec=spec,
            directory=str(data.get("directory", "")),
            legacy=False,
            **common,
        )
    if "script" in data:  # a Revision-5 script-only file: legacy, never run (AC7)
        return DashboardArtifact(
            spec=None,
            directory=str(data.get("directory", "")),
            legacy=True,
            script=str(data["script"]),
            **common,
        )
    logger.debug("skipping dashboard file with neither spec nor script: %s", path)
    return None
