"""Unit tests for the dashboard specification validator (DC-029).

Hermetic: every payload is an in-memory string or mapping -- no filesystem,
no network, no parsing of anything the card's allowlist does not name.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from daylight import models
from daylight.managers.dashboard_spec import (
    KIND_SOURCES,
    DashboardSpecError,
    parse_spec,
    validate_spec,
)

#: The documented example from the card, as a JSON string.
EXAMPLE_JSON = json.dumps(
    {
        "schema_version": 1,
        "title": "Files by type",
        "blocks": [
            {"kind": "summary_cards", "source": "summary"},
            {
                "kind": "bar_chart",
                "source": "extension_counts",
                "title": "Files by type",
                "top_n": 12,
                "color_token": "accent",
            },
        ],
    }
)

#: The same example as an in-memory mapping.
EXAMPLE_MAP: dict[str, Any] = json.loads(EXAMPLE_JSON)


def block(**overrides: Any) -> dict[str, Any]:
    """A minimal valid block with optional overrides."""
    return {"kind": "bar_chart", "source": "extension_counts", **overrides}


def spec_payload(**overrides: Any) -> dict[str, Any]:
    """A minimal valid top-level spec with optional overrides."""
    payload: dict[str, Any] = {
        "schema_version": 1,
        "title": "Files by type",
        "blocks": [block()],
    }
    payload.update(overrides)
    return payload


def without(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """``payload`` minus one key, to exercise required-key failures."""
    return {name: value for name, value in payload.items() if name != key}


# -- accepted payloads ---------------------------------------------------------


def test_parse_spec_accepts_the_documented_example() -> None:
    """The card's example parses to the frozen model, fields carried verbatim."""
    result = parse_spec(EXAMPLE_JSON)
    assert result.schema_version == 1
    assert result.title == "Files by type"
    assert result.blocks[0] == models.DashboardBlock(kind="summary_cards", source="summary")
    assert result.blocks[1] == models.DashboardBlock(
        kind="bar_chart",
        source="extension_counts",
        title="Files by type",
        top_n=12,
        color_token="accent",
    )


def test_parse_spec_applies_the_contract_defaults() -> None:
    """Blocks omitting ``top_n`` / ``color_token`` get top_n=8, color_token='accent'."""
    result = parse_spec(EXAMPLE_JSON)
    assert result.blocks[0].title == ""
    assert result.blocks[0].top_n == 8
    assert result.blocks[0].color_token == "accent"


def test_parse_spec_accepts_a_mapping_like_a_json_string() -> None:
    """Both accepted payload forms yield the identical frozen model."""
    assert parse_spec(EXAMPLE_MAP) == parse_spec(EXAMPLE_JSON)


def test_validate_spec_returns_the_frozen_models() -> None:
    result = validate_spec(EXAMPLE_MAP)
    assert isinstance(result, models.DashboardSpec)
    assert isinstance(result.blocks[1], models.DashboardBlock)
    assert validate_spec(EXAMPLE_JSON) == parse_spec(EXAMPLE_JSON)


def test_specs_and_blocks_are_frozen() -> None:
    """Neither levels of the parsed model may be mutated."""
    result = parse_spec(EXAMPLE_MAP)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.title = "other"
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.blocks[0].top_n = 4


def test_boundary_values_parse() -> None:
    """Title length 120, 20 blocks and top_n 1 and 50 are all inside the contract."""
    result = parse_spec(
        spec_payload(
            title="x" * 120,
            blocks=[block(title="t" * 120, top_n=1), block(top_n=50)],
        )
    )
    assert result.title == "x" * 120
    assert [b.top_n for b in result.blocks] == [1, 50]
    assert result.blocks[0].title == "t" * 120


def test_twenty_blocks_parse() -> None:
    """20 blocks is the accepted maximum."""
    result = parse_spec(spec_payload(blocks=[block() for _ in range(20)]))
    assert len(result.blocks) == 20


@pytest.mark.parametrize("kind,sources", sorted(KIND_SOURCES.items()))
def test_every_allowlisted_pair_parses(kind: str, sources: frozenset[str]) -> None:
    """Every kind->source pair the contract allows parses with the defaults."""
    for source in sorted(sources):
        result = parse_spec(spec_payload(blocks=[{"kind": kind, "source": source}]))
        assert result.blocks[0].kind == kind
        assert result.blocks[0].source == source
        assert result.blocks[0].top_n == 8
        assert result.blocks[0].color_token == "accent"


# -- rejected payloads -----------------------------------------------------------

INVALID: list[tuple[object, str]] = [
    # Payload level
    ("[]", "payload"),
    ('"just text"', "payload"),
    ("{", "invalid JSON"),
    # Top level
    (spec_payload(extra=True), "extra"),
    (without(spec_payload(), "schema_version"), "schema_version"),
    (spec_payload(schema_version="1"), "schema_version"),
    (spec_payload(schema_version=2), "schema_version"),
    (spec_payload(schema_version=True), "schema_version"),
    (spec_payload(schema_version=1.0), "schema_version"),
    (without(spec_payload(), "title"), "title"),
    (spec_payload(title=""), "title"),
    (spec_payload(title="   "), "title"),
    (spec_payload(title=7), "title"),
    (spec_payload(title="x" * 121), "title"),
    (without(spec_payload(), "blocks"), "blocks"),
    (spec_payload(blocks=[]), "blocks"),
    (spec_payload(blocks=[block() for _ in range(21)]), "blocks"),
    (spec_payload(blocks="no"), "blocks"),
    # Block level
    (spec_payload(blocks=[["summary_cards", "summary"]]), "blocks[0]"),
    (spec_payload(blocks=[{}]), "blocks[0].kind"),
    (spec_payload(blocks=[{"kind": "table", "source": "insights", "nonsense": 1}]), "nonsense"),
    (spec_payload(blocks=[block(kind="pie")]), "blocks[0].kind"),
    (spec_payload(blocks=[block(kind=5)]), "blocks[0].kind"),
    (spec_payload(blocks=[block(source="monthly")]), "blocks[0].source"),
    (spec_payload(blocks=[block(source=None)]), "blocks[0].source"),
    (spec_payload(blocks=[block(source="insights")]), "blocks[0].source"),
    (spec_payload(blocks=[block(source="category_sizes")]), "blocks[0].source"),
    (spec_payload(blocks=[block(source="daily_counts")]), "blocks[0].source"),
    (spec_payload(blocks=[block(kind="table", source="daily_counts")]), "blocks[0].source"),
    (spec_payload(blocks=[block(kind="heatmap", source="insights")]), "blocks[0].source"),
    (spec_payload(blocks=[block(top_n=0)]), "blocks[0].top_n"),
    (spec_payload(blocks=[block(top_n=51)]), "blocks[0].top_n"),
    (spec_payload(blocks=[block(top_n=-1)]), "blocks[0].top_n"),
    (spec_payload(blocks=[block(top_n="12")]), "blocks[0].top_n"),
    (spec_payload(blocks=[block(top_n=8.0)]), "blocks[0].top_n"),
    (spec_payload(blocks=[block(top_n=True)]), "blocks[0].top_n"),
    (spec_payload(blocks=[block(top_n=None)]), "blocks[0].top_n"),
    (spec_payload(blocks=[block(top_n=[8])]), "blocks[0].top_n"),
    (spec_payload(blocks=[block(title="t" * 121)]), "blocks[0].title"),
    (spec_payload(blocks=[block(title=3)]), "blocks[0].title"),
    (spec_payload(blocks=[block(color_token="rainbow")]), "blocks[0].color_token"),
    (spec_payload(blocks=[block(color_token=7)]), "blocks[0].color_token"),
    (spec_payload(blocks=[block(color_token=None)]), "blocks[0].color_token"),
]


@pytest.mark.parametrize("payload,path", INVALID)
def test_invalid_payloads_are_rejected_with_a_path(payload: object, path: str) -> None:
    """Every rejection raises the typed error and its message names the path."""
    with pytest.raises(DashboardSpecError) as raised:
        parse_spec(payload)
    assert path in str(raised.value)


def test_errors_carry_block_paths_by_position() -> None:
    """The second block is named as ``blocks[1].*`` exactly as the contract says."""
    payload = spec_payload(blocks=[block(), {"kind": "share_bar", "source": "size_buckets"}])
    with pytest.raises(DashboardSpecError) as raised:
        parse_spec(payload)
    assert "blocks[1].source" in str(raised.value)


def test_the_error_is_a_typed_value_error() -> None:
    """Callers can catch DashboardSpecError specifically or as ValueError."""
    assert issubclass(DashboardSpecError, ValueError)
    with pytest.raises(DashboardSpecError):
        validate_spec("{not json")
