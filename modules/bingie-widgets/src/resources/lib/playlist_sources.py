#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Discover server playlist nodes without persisting provider identifiers."""

from urllib.parse import parse_qs, urlparse


PROPERTY_PREFIX = "Jellyfin.wnodes."
JELLYFIN_PLUGIN_PREFIX = "plugin://plugin.video.jellyfin/"


def discover_playlist_routes(window):
    """Return the playlist routes currently published by Jellyfin for Kodi."""
    routes = []
    try:
        count = int(window.getProperty("Jellyfin.wnodes.total") or 0)
    except (TypeError, ValueError):
        count = 0

    for index in range(count):
        prefix = "%s%d." % (PROPERTY_PREFIX, index)
        if window.getProperty(prefix + "type").lower() != "playlists":
            continue
        route = (
            window.getProperty(prefix + "content")
            or window.getProperty(prefix + "path")
        )
        if route.startswith(JELLYFIN_PLUGIN_PREFIX) and route not in routes:
            routes.append(route)
    return routes


def playlist_identity(item):
    """Build an ephemeral dedupe key; IDs are never written to configuration."""
    path = item.get("file", "")
    folder = parse_qs(urlparse(path).query).get("folder", [])
    if folder:
        return ("folder", folder[0])
    return (
        "label",
        item.get("label", "").strip().casefold(),
        item.get("title", "").strip().casefold(),
    )


def merge_playlists(groups, limit=None):
    """Preserve provider order while merging duplicate playlist listings."""
    merged = []
    seen = set()
    for group in groups:
        for item in group:
            identity = playlist_identity(item)
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(item)
            if limit and len(merged) >= limit:
                return merged
    return merged
