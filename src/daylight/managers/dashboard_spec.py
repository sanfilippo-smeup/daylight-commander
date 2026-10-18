"""Strict parser/validator for AI dashboard specifications (DC-029, ADR-016).

The agent returns a declarative JSON object, and this module is the boundary:
pure data in, a frozen :class:`daylight.models.DashboardSpec` out, and a typed
:class:`DashboardSpecError` naming the offending path for anything outside the
allowlist. Nothing is executed: no ``eval``, ``exec``, ``import`` or
``subprocess`` and no filesystem access on the spec input --
:func:`json.loads` is the only thing that ever touches a payload string, and
the parsed values are only compared against allowlists.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from daylight.models import DashboardBlock, DashboardSpec

__all__ = [
    "BLOCK_KINDS",
    "BLOCK_SOURCES",
    "COLOR_TOKENS",
    "DashboardSpecError",
    "KIND_SOURCES",
    "MAX_BLOCKS",
    "MAX_TITLE",
    "MAX_TOP_N",
    "MIN_BLOCKS",
    "SCHEMA_VERSION",
    "parse_spec",
    "validate_spec",
]

#: The only schema version this contract accepts.
SCHEMA_VERSION = 1

#: Upper bound for the top-level and per-block titles, in characters.
MAX_TITLE = 120

#: A spec carries between 1 and 20 blocks.
MIN_BLOCKS = 1
MAX_BLOCKS = 20

#: Bounds of a block's ``top_n``.
MIN_TOP_N = 1
MAX_TOP_N = 50

#: Defaults applied to blocks that omit ``top_n`` / ``color_token``.
DEFAULT_TOP_N = 8
DEFAULT_COLOR_TOKEN = "accent"

#: Allowed ``kind`` values, in contract order.
BLOCK_KINDS: frozenset[str] = frozenset(
    ("summary_cards", "bar_chart", "share_bar", "histogram", "heatmap", "table")
)

#: Allowed ``source`` values, in contract order.
BLOCK_SOURCES: frozenset[str] = frozenset(
    (
        "summary",
        "extension_counts",
        "extension_sizes",
        "category_sizes",
        "size_buckets",
        "monthly_counts",
        "permission_counts",
        "daily_counts",
        "largest_files",
        "insights",
    )
)

#: The exact ``kind`` -> ``source`` combinations the contract allows.
KIND_SOURCES: Mapping[str, frozenset[str]] = {
    "summary_cards": frozenset(("summary",)),
    "bar_chart": frozenset(
        ("extension_counts", "extension_sizes", "monthly_counts", "permission_counts")
    ),
    "share_bar": frozenset(("category_sizes",)),
    "histogram": frozenset(("size_buckets",)),
    "heatmap": frozenset(("daily_counts",)),
    "table": frozenset(("largest_files", "insights")),
}

#: Allowed ``color_token`` values (theme tokens).
COLOR_TOKENS: frozenset[str] = frozenset(
    ("accent", "secondary", "success", "warning", "error", "foreground", "muted")
)

#: Top-level keys a spec object may carry.
_SPEC_KEYS: frozenset[str] = frozenset(("schema_version", "title", "blocks"))

#: Keys a block object may carry.
_BLOCK_KEYS: frozenset[str] = frozenset(("kind", "source", "title", "top_n", "color_token"))


class DashboardSpecError(ValueError):
    """A rejected dashboard specification; the message names the offending path."""


def _fail(path: str, problem: str) -> DashboardSpecError:
    """The error for ``problem`` at ``path``, e.g. ``blocks[1].source``."""
    return DashboardSpecError(f"{path}: {problem}")


def _reject_unknown_keys(data: Mapping[str, Any], allowed: frozenset[str], path: str) -> None:
    """Reject every key of ``data`` that is not in ``allowed``, naming its path."""
    for key in data:
        if isinstance(key, str) and key in allowed:
            continue
        where = f"{path}.{key}" if path else str(key)
        raise DashboardSpecError(f"{where}: unknown key")


def _payload_object(payload: object) -> Mapping[str, Any]:
    """A JSON object string or an already-decoded mapping, nothing else."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as error:
            raise DashboardSpecError(f"invalid JSON: {error}") from None
    if not isinstance(payload, Mapping):
        raise _fail("payload", "expected a JSON object")
    return payload


def _block_title(entry: Mapping[str, Any], path: str) -> str:
    """The optional block title: a string of at most :data:`MAX_TITLE`."""
    if "title" not in entry:
        return ""
    title = entry["title"]
    if not isinstance(title, str) or len(title) > MAX_TITLE:
        raise _fail(f"{path}.title", f"must be a string of at most {MAX_TITLE} characters")
    return title


def _block_top_n(entry: Mapping[str, Any], path: str) -> int:
    """The optional block ``top_n``: a true integer inside ``1..50``."""
    if "top_n" not in entry:
        return DEFAULT_TOP_N
    value = entry["top_n"]
    # ``type`` on purpose: bools and floats must never pass for integers.
    if type(value) is not int:
        raise _fail(f"{path}.top_n", f"must be an integer between 1 and 50, got {value!r}")
    if not MIN_TOP_N <= value <= MAX_TOP_N:
        raise _fail(f"{path}.top_n", f"must be between {MIN_TOP_N} and {MAX_TOP_N}, got {value}")
    return value


def _block_color_token(entry: Mapping[str, Any], path: str) -> str:
    """The optional block ``color_token``: one of :data:`COLOR_TOKENS`."""
    if "color_token" not in entry:
        return DEFAULT_COLOR_TOKEN
    token = entry["color_token"]
    if not isinstance(token, str) or token not in COLOR_TOKENS:
        raise _fail(f"{path}.color_token", f"unknown color token {token!r}")
    return token


def _validate_block(entry: Any, index: int) -> DashboardBlock:
    """One block object, validated against the allowlists, defaults applied."""
    path = f"blocks[{index}]"
    if not isinstance(entry, Mapping):
        raise _fail(path, "must be an object")
    _reject_unknown_keys(entry, _BLOCK_KEYS, path)
    for required in ("kind", "source"):
        if required not in entry:
            raise _fail(f"{path}.{required}", "is required")
    kind = entry["kind"]
    if not isinstance(kind, str) or kind not in BLOCK_KINDS:
        raise _fail(f"{path}.kind", f"unknown kind {kind!r}")
    source = entry["source"]
    if not isinstance(source, str) or source not in BLOCK_SOURCES:
        raise _fail(f"{path}.source", f"unknown source {source!r}")
    if source not in KIND_SOURCES[kind]:
        raise _fail(f"{path}.source", f"{source!r} is not a valid source for kind {kind!r}")
    return DashboardBlock(
        kind=kind,
        source=source,
        title=_block_title(entry, path),
        top_n=_block_top_n(entry, path),
        color_token=_block_color_token(entry, path),
    )


def _validate_mapping(data: Mapping[str, Any]) -> DashboardSpec:
    """The whole top-level object, validated key by key."""
    _reject_unknown_keys(data, _SPEC_KEYS, "")
    schema_version = data.get("schema_version")
    if "schema_version" not in data:
        raise _fail("schema_version", "is required")
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise _fail(
            "schema_version", f"must be the integer {SCHEMA_VERSION}, got {schema_version!r}"
        )
    title = data.get("title")
    if "title" not in data:
        raise _fail("title", "is required")
    if not isinstance(title, str) or not title.strip():
        raise _fail("title", "must be a non-empty string")
    if len(title) > MAX_TITLE:
        raise _fail("title", f"must be at most {MAX_TITLE} characters")
    raw_blocks = data.get("blocks")
    if "blocks" not in data:
        raise _fail("blocks", "is required")
    if not isinstance(raw_blocks, list):
        raise _fail("blocks", "must be an array")
    if not MIN_BLOCKS <= len(raw_blocks) <= MAX_BLOCKS:
        raise _fail("blocks", f"must contain between {MIN_BLOCKS} and {MAX_BLOCKS} blocks")
    blocks = tuple(_validate_block(entry, index) for index, entry in enumerate(raw_blocks))
    return DashboardSpec(schema_version=SCHEMA_VERSION, title=title, blocks=blocks)


def validate_spec(payload: str | Mapping[str, Any]) -> DashboardSpec:
    """Validate a spec payload -- a JSON object string or a mapping -- and
    return the frozen :class:`daylight.models.DashboardSpec`.

    :raises DashboardSpecError: with a path-bearing message (e.g.
        ``blocks[1].source``) for anything outside the allowlist.
    """
    return _validate_mapping(_payload_object(payload))


def parse_spec(payload: str | Mapping[str, Any]) -> DashboardSpec:
    """Parse a spec payload; the entry point for the agent's fenced JSON.

    Accepts the same inputs as :func:`validate_spec` -- a JSON object string or
    an already-decoded mapping -- and returns the same frozen model.
    """
    return validate_spec(payload)
