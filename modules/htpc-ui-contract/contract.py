"""Strict, dependency-free loader for the repository-owned UI contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONTRACT_ROOT = Path(__file__).with_name("contracts")
CONTRACT_NAMES = frozenset(("home", "playback"))
MAX_CONTRACT_BYTES = 64 * 1024


class ContractError(ValueError):
    """The contract is malformed or violates its semantic invariants."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _finite_number(value: str) -> None:
    raise ContractError("non-finite JSON number: %s" % value)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _keys(value: Any, expected: set[str], path: str) -> None:
    _require(isinstance(value, dict), "%s must be an object" % path)
    actual = set(value)
    _require(
        actual == expected,
        "%s keys differ: %s" % (path, sorted(actual ^ expected)),
    )


def _object(value: Any, expected: set[str], path: str) -> dict[str, Any]:
    _keys(value, expected, path)
    return value


def _nonempty_strings(values: Any, path: str) -> None:
    _require(
        isinstance(values, list) and values,
        "%s must be a non-empty list" % path,
    )
    _require(
        all(isinstance(value, str) and value for value in values),
        "%s must contain non-empty strings" % path,
    )
    _require(len(values) == len(set(values)), "%s contains duplicates" % path)


def loads_contract(name: str, text: str) -> dict[str, Any]:
    _require(name in CONTRACT_NAMES, "unknown contract: %s" % name)
    _require(
        len(text.encode("utf-8")) <= MAX_CONTRACT_BYTES,
        "%s contract exceeds 64 KiB" % name,
    )
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_finite_number,
        )
    except (TypeError, json.JSONDecodeError) as error:
        raise ContractError("invalid %s JSON: %s" % (name, error)) from error
    _require(isinstance(value, dict), "%s contract must be an object" % name)
    validate_contract(name, value)
    return value


def load_contract(name: str, root: Path | None = None) -> dict[str, Any]:
    path = (root or CONTRACT_ROOT) / (name + ".json")
    return loads_contract(name, path.read_text(encoding="utf-8"))


def validate_contract(name: str, value: dict[str, Any]) -> None:
    if name == "home":
        _validate_home(value)
    elif name == "playback":
        _validate_playback(value)
    else:
        raise ContractError("unknown contract: %s" % name)


def _validate_home(value: dict[str, Any]) -> None:
    _keys(
        value,
        {
            "schema_version",
            "owner",
            "window",
            "focus",
            "sidebar",
            "navigation",
            "rows",
            "library_roles",
        },
        "home",
    )
    _require(value["schema_version"] == 1, "unsupported Home schema")
    _require(value["owner"] == "htpc-ui", "Home owner must be htpc-ui")

    window = _object(value["window"], {"id", "controls"}, "home.window")
    controls = _object(
        window["controls"],
        {"bootstrap", "sidebar", "spotlight", "widget_group"},
        "home.window.controls",
    )
    _require(window["id"] == 10000, "Home property window must be 10000")
    _require(
        all(isinstance(control, int) and control > 0 for control in controls.values()),
        "Home control ids must be positive integers",
    )
    _require(len(set(controls.values())) == len(controls), "Home control ids must be unique")
    focus = value["focus"]
    _keys(
        focus,
        {
            "default_control",
            "spotlight_primary_action",
            "restore_sidebar_selection",
            "restore_destination_item",
        },
        "home.focus",
    )
    _require(
        focus.get("default_control") in controls,
        "default focus must name a control",
    )

    sidebar = value["sidebar"]
    _require(isinstance(sidebar, list) and sidebar, "Home sidebar must not be empty")
    labels: list[str] = []
    destinations: list[str] = []
    for index, entry in enumerate(sidebar):
        _keys(entry, {"label", "destination"}, "home.sidebar[%d]" % index)
        destination = entry["destination"]
        _keys(
            destination,
            {"kind", "target"},
            "home.sidebar[%d].destination" % index,
        )
        _require(
            destination["kind"] in ("route", "window"),
            "invalid sidebar destination kind",
        )
        _require(
            isinstance(destination["target"], str) and destination["target"],
            "sidebar target must be a string",
        )
        _require(
            isinstance(entry["label"], str) and entry["label"],
            "sidebar label must be a string",
        )
        labels.append(entry["label"])
        destinations.append(destination["target"])
    _require(len(labels) == len(set(labels)), "sidebar labels must be unique")
    _require(len(destinations) == len(set(destinations)), "sidebar destinations must be unique")

    _keys(
        value["navigation"],
        {"sidebar_right_inactive", "sidebar_right_active", "submenus"},
        "home.navigation",
    )

    route_names = set()
    row_keys = set()
    for index, row in enumerate(value["rows"]):
        _keys(
            row,
            {"key", "label", "art", "limit", "route", "empty"},
            "home.rows[%d]" % index,
        )
        _require(
            isinstance(row["key"], str) and row["key"],
            "Home row key must be a string",
        )
        _require(
            isinstance(row["label"], str) and row["label"],
            "Home row label must be a string",
        )
        _require(
            isinstance(row["route"], str) and row["route"],
            "Home row route must be a string",
        )
        _require(row["key"] not in row_keys, "Home row keys must be unique")
        _require(row["route"] not in route_names, "Home row routes must be unique")
        _require(row["art"] in ("landscape", "poster"), "unsupported Home row art")
        _require(
            isinstance(row["limit"], int) and 1 <= row["limit"] <= 100,
            "Home row limit is invalid",
        )
        _require(row["empty"] == "omit", "empty Home rows must be omitted")
        row_keys.add(row["key"])
        route_names.add(row["route"])

    roles = value["library_roles"]
    _require(isinstance(roles, dict), "library roles must be an object")
    _require(set(roles) == {"anime", "movies", "tvshows"}, "library roles differ")
    for role, definition in roles.items():
        expected_keys = {"route"} if role == "anime" else {"route", "genre_route"}
        _keys(definition, expected_keys, "home.library_roles.%s" % role)
        _require(definition.get("route") == "library.%s" % role, "library role route differs")


def _validate_playback(value: dict[str, Any]) -> None:
    _keys(
        value,
        {
            "schema_version",
            "protocol_version",
            "property_window",
            "service",
            "seek",
            "preview",
            "chapters",
            "controls",
            "publication",
        },
        "playback",
    )
    _require(value["schema_version"] == 1, "unsupported playback schema")
    _require(value["protocol_version"] == "2", "unsupported service protocol")
    _require(value["property_window"] == 10000, "playback property window must be Home")

    _keys(value["service"], {"ready", "protocol"}, "playback.service")

    seek = value["seek"]
    _keys(
        seek,
        {
            "prefix",
            "request",
            "request_schema",
            "controller_fields",
            "view_fields",
            "slots",
            "slot_fields",
            "commit_property",
            "events",
        },
        "playback.seek",
    )
    _require(seek["prefix"] == "htpc.seek.", "seek prefix differs")
    _require(seek["request_schema"] == 1, "seek request schema differs")
    _nonempty_strings(seek["controller_fields"], "playback.seek.controller_fields")
    _nonempty_strings(seek["view_fields"], "playback.seek.view_fields")
    _nonempty_strings(seek["slots"], "playback.seek.slots")
    _nonempty_strings(seek["slot_fields"], "playback.seek.slot_fields")
    _nonempty_strings(seek["events"], "playback.seek.events")
    _require(
        seek["slots"] == ["a", "b"],
        "playback requires two ordered view slots",
    )
    _require(
        seek["commit_property"] == "viewslot"
        and "viewslot" in seek["view_fields"],
        "viewslot must be the commit property",
    )

    preview = value["preview"]
    _keys(
        preview,
        {"property", "schema", "statuses"},
        "playback.preview",
    )
    _require(preview.get("schema") == 2, "preview schema differs")
    _nonempty_strings(preview.get("statuses"), "playback.preview.statuses")
    _require(
        "ready" in preview["statuses"] and "unavailable" in preview["statuses"],
        "preview terminal states are incomplete",
    )

    chapters = value["chapters"]
    _keys(
        chapters,
        {
            "schema",
            "available",
            "manifest",
            "token",
            "playback",
            "revision",
            "ui_available",
            "ui_open",
        },
        "playback.chapters",
    )
    _require(chapters.get("schema") == 1, "chapter schema differs")
    chapter_properties = [
        chapters.get(key)
        for key in (
            "available",
            "manifest",
            "token",
            "playback",
            "revision",
            "ui_available",
            "ui_open",
        )
    ]
    _require(
        all(isinstance(item, str) and item for item in chapter_properties),
        "chapter properties must be named",
    )
    _require(
        len(set(chapter_properties)) == len(chapter_properties),
        "chapter properties must be unique",
    )

    controls = value["controls"]
    _keys(
        controls,
        {"top_bar_group", "top_bar", "play_pause", "timeline"},
        "playback.controls",
    )
    _require(
        all(isinstance(control, int) and control > 0 for control in controls.values()),
        "playback control ids must be positive integers",
    )
    _require(len(set(controls.values())) == len(controls), "playback control ids must be unique")
    publication = value["publication"]
    _require(
        isinstance(publication, dict),
        "playback.publication must be an object",
    )
    _require(
        set(publication)
        == {
            "inactive_slot_first",
            "commit_last",
            "chapter_available_last",
            "preview_response_atomic",
        },
        "playback publication guarantees differ",
    )
    _require(
        all(item is True for item in publication.values()),
        "all atomic publication guarantees are required",
    )


def playback_property_names(contract: dict[str, Any]) -> frozenset[str]:
    """Return every Window property owned by the playback protocol."""
    seek = contract["seek"]
    prefix = seek["prefix"]
    properties = set(contract["service"].values())
    properties.add(seek["request"])
    properties.add(contract["preview"]["property"])
    properties.update(
        contract["chapters"][key]
        for key in (
            "available",
            "manifest",
            "token",
            "playback",
            "revision",
            "ui_available",
            "ui_open",
        )
    )
    properties.update(prefix + field for field in seek["controller_fields"])
    properties.update(prefix + field for field in seek["view_fields"])
    properties.update(
        prefix + slot + "." + field
        for slot in seek["slots"]
        for field in seek["slot_fields"]
    )
    return frozenset(properties)
