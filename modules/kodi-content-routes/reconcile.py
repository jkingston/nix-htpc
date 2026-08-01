#!/usr/bin/env python3
"""Publish stable appliance aliases for Jellyfin-generated Kodi nodes."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


ROLE_TAGS = {
    "anime": "Anime",
    "tvshows": "Shows",
}


class RouteError(RuntimeError):
    pass


def node_tag(node_directory: Path) -> str | None:
    try:
        root = ET.parse(node_directory / "all.xml").getroot()
    except (OSError, ET.ParseError):
        return None
    for rule in root.findall("rule"):
        if rule.get("field") == "tag" and rule.get("operator") == "is":
            value = rule.findtext("value")
            if value:
                return value
    return None


def discover_routes(library_root: Path) -> dict[str, Path]:
    matches: dict[str, list[Path]] = {role: [] for role in ROLE_TAGS}
    for candidate in sorted(library_root.glob("jellyfintvshows*")):
        if not candidate.is_dir():
            continue
        tag = node_tag(candidate)
        for role, expected_tag in ROLE_TAGS.items():
            if tag == expected_tag:
                matches[role].append(candidate)

    ambiguous = {
        role: candidates
        for role, candidates in matches.items()
        if len(candidates) > 1
    }
    if ambiguous:
        details = ", ".join(
            "%s=%s" % (role, len(candidates))
            for role, candidates in sorted(ambiguous.items())
        )
        raise RouteError("ambiguous Jellyfin content roles: %s" % details)
    return {
        role: candidates[0]
        for role, candidates in matches.items()
        if candidates
    }


def publish_alias(alias: Path, target: Path) -> bool:
    relative_target = os.path.relpath(target, alias.parent)
    if alias.is_symlink() and os.readlink(alias) == relative_target:
        return False
    if alias.exists() and not alias.is_symlink():
        raise RouteError("refusing to replace non-symlink route: %s" % alias)
    temporary = alias.with_name(".%s.new" % alias.name)
    try:
        if temporary.is_symlink() or temporary.exists():
            temporary.unlink()
        temporary.symlink_to(relative_target, target_is_directory=True)
        os.replace(temporary, alias)
    finally:
        if temporary.is_symlink() or temporary.exists():
            temporary.unlink()
    return True


def reconcile(library_root: Path) -> dict[str, str]:
    library_root.mkdir(parents=True, exist_ok=True)
    routes = discover_routes(library_root)
    published: dict[str, str] = {}
    for role, target in sorted(routes.items()):
        alias = library_root / ("htpc-%s" % role)
        publish_alias(alias, target)
        published[role] = alias.name
    return published


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--library-root",
        type=Path,
        default=Path("/home/htpc/.kodi/userdata/library/video"),
    )
    arguments = parser.parse_args(argv)
    try:
        routes = reconcile(arguments.library_root)
    except RouteError as error:
        print("htpc-content-routes: %s" % error, file=sys.stderr)
        return 1
    for role, alias in sorted(routes.items()):
        print("%s=%s" % (role, alias))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
