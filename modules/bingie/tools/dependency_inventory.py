#!/usr/bin/env python3
"""Validate BINGIE dependencies or passively capture explicit manifest paths.

Capture never invokes Kodi or a shell and writes only sanitized stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

MANDATORY_IDS = [
    "script.bingie.helper",
    "script.bingie.toolbox",
    "script.bingie.widgets",
    "script.skinshortcuts",
    "resource.images.studios.coloured",
    "plugin.video.tmdb.bingie.helper",
    "plugin.program.autocompletion",
]
CORE_PREFIXES = ("xbmc.",)
ADDON_ID = r"(?:plugin|resource|script|service)\.[A-Za-z0-9][A-Za-z0-9_.-]*"
REFERENCE_PATTERNS = [
    re.compile(rf"(?:System\.HasAddon|InstallAddon|RunAddon|RunScript)\(\s*({ADDON_ID})"),
    re.compile(rf"(?:plugin|resource)://({ADDON_ID})(?=[/?),&\s<])"),
    re.compile(rf"\$ADDON\[\s*({ADDON_ID})(?=[\s\]])"),
    re.compile(rf'<param\s+name="addon"\s+value="({ADDON_ID})"'),
]
FAMILY_PATTERN = re.compile(
    rf"(?:addontype|resourceaddon)\s*=\s*({ADDON_ID})(?=[,)&\s<])"
)
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PRIVATE_PATH_PATTERN = re.compile(r"(?:^|[\s\"'])(?:/Users/|/home/|/root/)")
IPV4_PATTERN = re.compile(r"(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])")
MAC_PATTERN = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])")
PRIVATE_KEYS = {"device", "host", "hostname", "ip", "ip_address", "machine", "user", "username"}
GENERIC_CONTENT_IDS = {"plugin.video"}
CLASSIFICATIONS = {
    "both_exact_match", "both_same_version_manifest_mismatch",
    "both_version_mismatch", "missing", "nix_only", "userdata_only",
}
PLANNED_RESOLUTIONS = {"package_in_nix_m0_9", "package_in_nix_m0_9_then_remove_in_m1_10"}
PROVENANCE_BY_CLASSIFICATION = {
    "missing": "unverified_missing",
    "nix_only": "nix_pinned",
    "userdata_only": "unverified_deployed_userdata",
    "both_exact_match": "nix_pinned_with_deployed_userdata",
    "both_same_version_manifest_mismatch": "nix_pinned_with_deployed_userdata",
    "both_version_mismatch": "nix_pinned_with_deployed_userdata",
}
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_~-]{0,63}$")
NIX_QUERY = "nix-store --query --requisites /run/current-system"
NIX_COVERAGE = "all_addon_manifests_in_recursive_requisites"
NIX_BASENAME_PATTERN = re.compile(r"^[a-z0-9]{32}-[A-Za-z0-9.+_=-]+$")

class InventoryError(ValueError):
    """Raised for malformed or ambiguous evidence."""

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

def _reject_constant(value: str) -> None:
    raise InventoryError(f"non-finite JSON number: {value}")

def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result

def load_report(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise InventoryError(f"invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise InventoryError("report root must be an object")
    return value, raw

def parse_manifest(path: Path) -> dict[str, Any]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as error:
        raise InventoryError(f"cannot parse {path}: {error}") from error
    if root.tag != "addon" or not root.get("id") or not root.get("version"):
        raise InventoryError(f"{path} is not an add-on manifest")
    imports = []
    for item in root.findall("./requires/import"):
        addon_id = item.get("addon")
        version = item.get("version")
        if not addon_id or not version:
            raise InventoryError(f"incomplete import in {path}")
        imports.append(
            {
                "id": addon_id,
                "minimum_version": version,
                "optional": item.get("optional", "false").lower() == "true",
            }
        )
    return {"id": root.get("id"), "version": root.get("version"), "imports": imports}

def source_references(skin_root: Path, imported_ids: Iterable[str]) -> dict[str, list[str]]:
    imported = set(imported_ids)
    addons: set[str] = set()
    families: set[str] = set()
    source_paths = (
        path for path in skin_root.rglob("*") if path.suffix in {".xml", ".xsp"}
    )
    for path in sorted(source_paths):
        text = path.read_text(encoding="utf-8")
        for pattern in REFERENCE_PATTERNS:
            addons.update(pattern.findall(text))
        families.update(FAMILY_PATTERN.findall(text))
    addons.difference_update(imported)
    addons.difference_update(families)
    addons.difference_update(GENERIC_CONTENT_IDS)
    return {"addons": sorted(addons), "resource_families": sorted(families)}

def classify(nix_closure: Any, userdata: Any) -> str:
    if nix_closure is None and userdata is None:
        return "missing"
    if userdata is None:
        return "nix_only"
    if nix_closure is None:
        return "userdata_only"
    if nix_closure.get("version") != userdata.get("version"):
        return "both_version_mismatch"
    if nix_closure.get("addon_xml_sha256") == userdata.get("addon_xml_sha256"):
        return "both_exact_match"
    return "both_same_version_manifest_mismatch"

def _exact_keys(value: Any, expected: set[str], label: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return False
    actual = set(value)
    if actual != expected:
        errors.append(f"{label} keys: expected {sorted(expected)}, got {sorted(actual)}")
        return False
    return True

def _privacy_errors(value: Any, trail: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in PRIVATE_KEYS:
                errors.append(f"{trail}.{key} is host-identifying metadata")
            errors.extend(_privacy_errors(child, f"{trail}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_privacy_errors(child, f"{trail}[{index}]"))
    elif isinstance(value, str):
        if PRIVATE_PATH_PATTERN.search(value):
            errors.append(f"{trail} contains a private filesystem path")
        if not trail.endswith(("version", "minimum_version")) and IPV4_PATTERN.search(value):
            errors.append(f"{trail} contains an IP address")
        if MAC_PATTERN.search(value):
            errors.append(f"{trail} contains a MAC address")
    return errors

def validate_report(report: dict[str, Any], raw: str, skin_root: Path) -> list[str]:
    errors: list[str] = []
    if raw != canonical_json(report):
        errors.append("report is not canonical JSON (UTF-8, sorted keys, two-space indent)")
    if not _exact_keys(
        report,
        {
            "core_imports", "dependencies", "nix_closure_evidence",
            "referenced_not_imported", "schema_version", "subject",
        },
        "report",
        errors,
    ):
        return errors
    if type(report["schema_version"]) is not int or report["schema_version"] != 1:
        errors.append("schema_version must be 1")

    subject = report["subject"]
    subject_keys = {"addon_id", "addon_version", "declaration", "declaration_sha256"}
    if not _exact_keys(subject, subject_keys, "subject", errors):
        return errors
    if not all(isinstance(subject[key], str) and subject[key] for key in subject_keys):
        errors.append("subject fields must be non-empty strings")
        return errors
    declaration = skin_root / subject["declaration"]
    manifest = parse_manifest(declaration)
    if subject["addon_id"] != manifest["id"] or subject["addon_version"] != manifest["version"]:
        errors.append("subject does not match the declared add-on")
    if subject["declaration_sha256"] != sha256(declaration):
        errors.append("declaration_sha256 does not match src/addon.xml")

    declared_core = [item for item in manifest["imports"] if item["id"].startswith(CORE_PREFIXES)]
    declared_non_core = [item for item in manifest["imports"] if not item["id"].startswith(CORE_PREFIXES)]
    if report["core_imports"] != declared_core:
        errors.append("core_imports drifted from src/addon.xml")
    if [item["id"] for item in declared_non_core] != MANDATORY_IDS:
        errors.append("src/addon.xml must contain the seven mandatory imports in declaration order")

    closure_evidence = report["nix_closure_evidence"]
    closure_keys = {
        "coverage", "mandatory_addons_found", "method", "system_closure_basename"
    }
    if not _exact_keys(closure_evidence, closure_keys, "nix_closure_evidence", errors):
        return errors
    found_ids = closure_evidence["mandatory_addons_found"]
    if (
        closure_evidence["method"] != NIX_QUERY
        or closure_evidence["coverage"] != NIX_COVERAGE
        or not isinstance(closure_evidence["system_closure_basename"], str)
        or not NIX_BASENAME_PATTERN.fullmatch(
            closure_evidence["system_closure_basename"]
        )
        or not isinstance(found_ids, list)
        or not all(addon_id in MANDATORY_IDS for addon_id in found_ids)
        or found_ids != sorted(set(found_ids))
    ):
        errors.append("nix_closure_evidence is not complete, sanitized evidence")
        found_ids = []

    dependencies = report["dependencies"]
    if not isinstance(dependencies, list):
        errors.append("dependencies must be a list")
        return errors
    declaration_view = [
        {
            "id": item.get("id"),
            "minimum_version": item.get("minimum_version"),
            "optional": item.get("optional"),
        }
        for item in dependencies
        if isinstance(item, dict)
    ]
    if declaration_view != declared_non_core:
        errors.append("dependencies drifted from src/addon.xml declarations")

    dependency_keys = {
        "classification", "id", "minimum_version", "nix_closure", "optional",
        "planned_resolution", "provenance_status", "runtime_enabled", "userdata",
    }
    observation_keys = {"addon_xml_sha256", "version"}
    for index, dependency in enumerate(dependencies):
        label = f"dependencies[{index}]"
        if not _exact_keys(dependency, dependency_keys, label, errors):
            continue
        if not isinstance(dependency["id"], str) or not dependency["id"]:
            errors.append(f"{label}.id must be a non-empty string")
        if not isinstance(dependency["minimum_version"], str) or not dependency["minimum_version"]:
            errors.append(f"{label}.minimum_version must be a non-empty string")
        if not isinstance(dependency["optional"], bool):
            errors.append(f"{label}.optional must be boolean")
        if (
            not isinstance(dependency["classification"], str)
            or dependency["classification"] not in CLASSIFICATIONS
        ):
            errors.append(f"{label}.classification is not supported")
        observations_valid = True
        for scope in ("nix_closure", "userdata"):
            observation = dependency[scope]
            if observation is not None:
                if not _exact_keys(observation, observation_keys, f"{label}.{scope}", errors):
                    observations_valid = False
                    continue
                if (
                    not isinstance(observation["addon_xml_sha256"], str)
                    or not HASH_PATTERN.fullmatch(observation["addon_xml_sha256"])
                ):
                    errors.append(f"{label}.{scope}.addon_xml_sha256 must be lowercase SHA-256")
                if not isinstance(observation["version"], str) or not observation["version"]:
                    errors.append(f"{label}.{scope}.version must be a non-empty string")
        if not isinstance(dependency["runtime_enabled"], bool):
            errors.append(f"{label}.runtime_enabled must be boolean")
        if observations_valid:
            expected = classify(dependency["nix_closure"], dependency["userdata"])
            if dependency["classification"] != expected:
                errors.append(f"{label}.classification must be {expected}")
            if dependency["provenance_status"] != PROVENANCE_BY_CLASSIFICATION[expected]:
                errors.append(f"{label}.provenance_status is inconsistent with {expected}")
        in_closure = dependency["id"] in found_ids
        if in_closure != (dependency["nix_closure"] is not None):
            errors.append(f"{label}.nix_closure contradicts nix_closure_evidence")
        if (
            not isinstance(dependency["planned_resolution"], str)
            or dependency["planned_resolution"] not in PLANNED_RESOLUTIONS
        ):
            errors.append(f"{label}.planned_resolution is not supported")

    expected_references = source_references(skin_root, [item["id"] for item in manifest["imports"]])
    references = report["referenced_not_imported"]
    if not _exact_keys(references, {"addons", "resource_families"}, "referenced_not_imported", errors):
        return errors
    for key in ("addons", "resource_families"):
        values = references[key]
        if (
            not isinstance(values, list)
            or not all(isinstance(value, str) for value in values)
            or values != sorted(set(values))
        ):
            errors.append(f"referenced_not_imported.{key} must be sorted and unique")
    if references != expected_references:
        errors.append("referenced_not_imported drifted from XML source references")
    errors.extend(_privacy_errors(report))
    return errors

def _resolve_manifest(path: Path) -> Path:
    candidate = path / "addon.xml" if path.is_dir() else path
    if candidate.name != "addon.xml" or not candidate.is_file():
        raise InventoryError(f"expected an addon.xml file or add-on directory: {path}")
    return candidate

def capture_paths(roots: Iterable[Path], path_lists: Iterable[Path]) -> list[Path]:
    paths: set[Path] = set()
    for root in roots:
        if (root / "addon.xml").is_file():
            paths.add((root / "addon.xml").resolve())
        elif root.is_dir():
            paths.update(path.resolve() for path in root.glob("*/addon.xml"))
        else:
            raise InventoryError(f"capture root is not a directory: {root}")
    for path_list in path_lists:
        for line in path_list.read_text(encoding="utf-8").splitlines():
            entry = line.strip()
            if entry and not entry.startswith("#"):
                explicit_path = Path(entry)
                if not explicit_path.is_absolute():
                    raise InventoryError("path-list entries must be absolute")
                paths.add(_resolve_manifest(explicit_path).resolve())
    return sorted(paths)

def capture(scope: str, roots: Iterable[Path], path_lists: Iterable[Path]) -> dict[str, Any]:
    if scope not in {"nix_closure", "userdata"}:
        raise InventoryError(f"unsupported capture scope: {scope}")
    addons: dict[str, dict[str, str]] = {}
    for path in capture_paths(roots, path_lists):
        manifest = parse_manifest(path)
        addon_id = manifest["id"]
        if addon_id not in MANDATORY_IDS:
            continue
        if not VERSION_PATTERN.fullmatch(manifest["version"]):
            raise InventoryError(f"invalid version for mandatory add-on: {addon_id}")
        if addon_id in addons:
            raise InventoryError(f"duplicate add-on id in capture: {addon_id}")
        addons[addon_id] = {"addon_xml_sha256": sha256(path), "version": manifest["version"]}
    return {
        "addons": [{"id": addon_id, **addons[addon_id]} for addon_id in sorted(addons)],
        "schema_version": 1,
        "scope": scope,
    }

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="validate the committed report")
    check.add_argument("--report", type=Path)
    check.add_argument("--skin-root", type=Path)
    scan = commands.add_parser("capture", help="print sanitized manifest observations")
    scan.add_argument("--scope", required=True, choices=("nix_closure", "userdata"))
    scan.add_argument("--root", action="append", default=[], type=Path)
    scan.add_argument("--path-list", action="append", default=[], type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            if not args.root and not args.path_list:
                raise InventoryError("capture needs --root and/or --path-list")
            sys.stdout.write(canonical_json(capture(args.scope, args.root, args.path_list)))
            return 0
        module_root = Path(__file__).resolve().parents[1]
        skin_root = args.skin_root or Path(os.environ.get("BINGIE_SKIN_ROOT", module_root / "src"))
        report_path = args.report or module_root / "audit" / "dependency-inventory.json"
        report, raw = load_report(report_path)
        errors = validate_report(report, raw, skin_root)
        for error in errors:
            print(f"dependency inventory: {error}", file=sys.stderr)
        return 1 if errors else 0
    except (InventoryError, OSError, TypeError, KeyError) as error:
        print(f"dependency inventory: {error}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
