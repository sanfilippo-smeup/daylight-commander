"""Per-user persistence for saved AI dashboards (DC-024, FR-806, ADR-014).

A dashboard is one JSON file under the user's data directory
(``$XDG_DATA_HOME/daylight/dashboards``, falling back to
``~/.local/share/daylight/dashboards``), holding the name, the prompt that
produced it, the generated script and timestamps. :class:`DashboardStore`
writes, lists (newest first), loads and deletes those files; an explicit
``root`` overrides the default location so tests stay inside ``tmp_path``.

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

from daylight.models import DashboardArtifact

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

    def save(self, *, name: str, prompt: str, script: str) -> DashboardArtifact:
        """Write one JSON artifact and return it (AC8)."""
        self.root.mkdir(parents=True, exist_ok=True)
        now = time()
        artifact = DashboardArtifact(
            id=uuid.uuid4().hex,
            name=name,
            prompt=prompt,
            script=script,
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
        "script": artifact.script,
        "created": artifact.created,
        "modified": artifact.modified,
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _read_artifact(path: Path) -> DashboardArtifact | None:
    """Parse one artifact file; ``None`` when it is missing or malformed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return DashboardArtifact(
            id=str(data["id"]),
            name=str(data["name"]),
            prompt=str(data["prompt"]),
            script=str(data["script"]),
            created=float(data["created"]),
            modified=float(data["modified"]),
        )
    except (KeyError, TypeError, ValueError):
        logger.debug("skipping malformed dashboard file: %s", path, exc_info=True)
        return None
