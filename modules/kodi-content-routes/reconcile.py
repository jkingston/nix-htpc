#!/usr/bin/env python3
"""Publish stable appliance aliases for Jellyfin-generated Kodi nodes."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys
import xml.etree.ElementTree as ET


ROLE_TAGS = {
    "anime": "Anime",
    "tvshows": "Shows",
}
REQUIRED_ENDPOINTS = ("all.xml", "nextepisodes.xml", "recent.xml")
MAX_NODE_BYTES = 64 * 1024


class RouteError(RuntimeError):
    pass


def _regular_node_file(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RouteError("missing content endpoint: %s" % path.name) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise RouteError("content endpoint is not a regular file: %s" % path)
    if metadata.st_size > MAX_NODE_BYTES:
        raise RouteError("content endpoint is too large: %s" % path)
    try:
        return path.read_bytes()
    except OSError as error:
        raise RouteError("unable to read content endpoint: %s" % path) from error


def node_tag(node_directory: Path) -> str | None:
    for endpoint in REQUIRED_ENDPOINTS:
        _regular_node_file(node_directory / endpoint)
    try:
        root = ET.fromstring(_regular_node_file(node_directory / "all.xml"))
    except ET.ParseError as error:
        raise RouteError("invalid Kodi content node: %s" % node_directory) from error
    for rule in root.findall("rule"):
        if rule.get("field") == "tag" and rule.get("operator") == "is":
            value = rule.findtext("value")
            if value:
                return value
    return None


def discover_routes(library_root: Path) -> dict[str, Path]:
    matches: dict[str, list[Path]] = {role: [] for role in ROLE_TAGS}
    for candidate in sorted(library_root.glob("jellyfintvshows*")):
        try:
            metadata = candidate.lstat()
        except OSError:
            continue
        if not stat.S_ISDIR(metadata.st_mode):
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
    aliases = {
        role: library_root / ("htpc-%s" % role) for role in ROLE_TAGS
    }
    for alias in aliases.values():
        if alias.exists() and not alias.is_symlink():
            raise RouteError("refusing to replace non-symlink route: %s" % alias)
    published: dict[str, str] = {}
    for role, target in sorted(routes.items()):
        alias = aliases[role]
        publish_alias(alias, target)
        published[role] = alias.name
    for role in sorted(set(aliases) - set(routes)):
        alias = aliases[role]
        if alias.is_symlink():
            alias.unlink()
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
