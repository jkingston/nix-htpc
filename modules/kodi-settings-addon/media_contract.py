from __future__ import absolute_import, division, print_function

import json


HOME_WINDOW_ID = 10000

# Service capability lease consumed by the BINGIE fallback condition. Keep
# this namespace distinct from the per-transaction htpc.seek.* properties.
SERVICE_READY = "htpc.service.ready"
SERVICE_PROTOCOL = "htpc.service.protocol"
SERVICE_PROTOCOL_VERSION = "2"
CHAPTER_AVAILABLE = "htpc.chapter.available"
CHAPTER_OPEN = "htpc.chapter.open"

SEEK_PREFIX = "htpc.seek."
SEEK_PROPERTY_KEYS = (
    "active",
    "generation",
    "state",
    "mode",
    "source",
    "targetseconds",
    "percent",
    "time",
    "delta",
    "confirm",
    "modal",
    "controllerpaused",
    "wasplaying",
    "playbackepoch",
    "hold",
    "holdreleased",
    "previewready",
    "previewpath",
)

# Jellyfin lane chapter contract. The producer publishes the sanitized manifest
# first, its commit token next, and AVAILABLE last.
CHAPTERS_AVAILABLE = "jellyfin.htpc.chapters.available"
CHAPTERS_MANIFEST = "jellyfin.htpc.chapters.manifest"
CHAPTERS_TOKEN = "jellyfin.htpc.chapters.token"
CHAPTERS_PLAYBACK = "jellyfin.htpc.chapters.playback"
CHAPTERS_REVISION = "jellyfin.htpc.chapters.revision"
CHAPTER_CONTRACT_VERSION = 1

# Exact preview producer writes path/component fields first and the JSON token
# last. A chapter image is never valid for this namespace.
PREVIEW_PATH = "jellyfin.htpc.seekpreview"
PREVIEW_TOKEN = "jellyfin.htpc.seekpreviewtoken"
PREVIEW_PLAYBACK = "jellyfin.htpc.seekpreviewplayback"
PREVIEW_GENERATION = "jellyfin.htpc.seekpreviewgeneration"
PREVIEW_TARGET = "jellyfin.htpc.seekpreviewtarget"
PREVIEW_SAMPLE = "jellyfin.htpc.seekpreviewsample"
PREVIEW_FRAME = "jellyfin.htpc.seekpreviewframe"
PREVIEW_REVISION = "jellyfin.htpc.seekpreviewrevision"


def _text(value):
    if value is None:
        return ""
    return str(value)


def _same_integer(left, right):
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return False


def _same_number(left, right, tolerance=0.0005):
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def parse_chapter_payload(serialized, expected_token=None):
    """Return validated chapter records from the Jellyfin-only contract.

    Records must explicitly declare kind="chapter". Bookmark/resume shaped
    objects are rejected even if they happen to carry a timestamp and image.
    """
    if not serialized:
        return []
    try:
        payload = json.loads(serialized)
    except (TypeError, ValueError):
        return []

    if not isinstance(payload, dict):
        return []
    if payload.get("schema") != CHAPTER_CONTRACT_VERSION:
        return []

    token = _text(payload.get("playback"))
    if not token:
        return []
    if expected_token is not None and token != _text(expected_token):
        return []

    raw_chapters = payload.get("entries")
    if not isinstance(raw_chapters, list):
        return []

    normalized = []
    seen_starts = set()
    for position, raw in enumerate(raw_chapters):
        if not isinstance(raw, dict):
            continue
        explicit_kind = _text(raw.get("kind") or raw.get("type")).lower()
        if explicit_kind != "chapter":
            continue
        try:
            start = float(raw["time_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < 0:
            continue
        start_key = int(round(start * 1000.0))
        if start_key in seen_starts:
            continue

        label = _text(raw.get("label")).strip()
        if not label:
            label = "Chapter %d" % (len(normalized) + 1)
        try:
            chapter_index = int(raw.get("index", position))
        except (TypeError, ValueError):
            chapter_index = position

        seen_starts.add(start_key)
        normalized.append(
            {
                "kind": "chapter",
                "index": chapter_index,
                "start_seconds": start,
                "label": label,
                "image_path": _text(raw.get("image")).strip(),
                "playback_token": token,
            }
        )

    normalized.sort(key=lambda chapter: chapter["start_seconds"])
    return normalized


def chapter_contract_available(properties, minimum=2):
    """Validate the property-level publish ordering and visible chapter set."""
    if properties.get(CHAPTERS_AVAILABLE) != "true":
        return False
    playback = properties.get(CHAPTERS_PLAYBACK)
    if not playback:
        return False
    if not properties.get(CHAPTERS_REVISION):
        return False

    try:
        token = json.loads(properties.get(CHAPTERS_TOKEN) or "")
    except (TypeError, ValueError):
        return False
    if not isinstance(token, dict) or token.get("schema") != 1:
        return False
    if _text(token.get("playback")) != playback:
        return False
    if _text(token.get("revision")) != properties.get(CHAPTERS_REVISION):
        return False
    try:
        manifest = json.loads(properties.get(CHAPTERS_MANIFEST) or "")
    except (TypeError, ValueError):
        return False
    if not isinstance(manifest, dict):
        return False
    if manifest.get("schema") != 1:
        return False
    if _text(manifest.get("playback")) != playback:
        return False
    if _text(manifest.get("revision")) != properties.get(CHAPTERS_REVISION):
        return False
    if _text(token.get("manifest_revision")) != _text(
        manifest.get("manifest_revision")
    ):
        return False
    try:
        expected_count = int(manifest.get("expected_count"))
    except (TypeError, ValueError):
        return False

    chapters = parse_chapter_payload(
        properties.get(CHAPTERS_MANIFEST),
        expected_token=playback,
    )
    if len(chapters) < int(minimum) or len(chapters) != expected_count:
        return False

    # AVAILABLE is an all-retained barrier; never accept a mixed rail with
    # later blank entries merely because its first screenful is complete.
    return all(chapter["image_path"] for chapter in chapters)


def validated_preview(properties, seek_snapshot):
    """Return an exact preview path only after the JSON commit token matches."""
    if not seek_snapshot.get("active"):
        return ""
    path = properties.get(PREVIEW_PATH) or ""
    serialized = properties.get(PREVIEW_TOKEN) or ""
    if not path or not serialized:
        return ""
    try:
        token = json.loads(serialized)
    except (TypeError, ValueError):
        return ""
    if not isinstance(token, dict) or token.get("schema") != 1:
        return ""
    try:
        generation = int(token.get("seek_generation"))
        target = int(token.get("target_seconds"))
        frame = int(token.get("frame_index"))
        revision = int(token.get("revision"))
        float(token.get("sample_seconds"))
    except (TypeError, ValueError):
        return ""
    if generation != int(seek_snapshot.get("generation", -1)):
        return ""
    if target != int(seek_snapshot.get("target_seconds", -1)):
        return ""
    if frame < 0 or revision < 0 or not _text(token.get("playback")):
        return ""
    if _text(properties.get(PREVIEW_PLAYBACK)) != _text(
        token.get("playback")
    ):
        return ""
    if not _same_integer(
        properties.get(PREVIEW_GENERATION),
        token.get("seek_generation"),
    ):
        return ""
    if not _same_number(
        properties.get(PREVIEW_TARGET),
        token.get("target_seconds"),
    ):
        return ""
    if not _same_number(
        properties.get(PREVIEW_SAMPLE),
        token.get("sample_seconds"),
    ):
        return ""
    if not _same_integer(
        properties.get(PREVIEW_FRAME),
        token.get("frame_index"),
    ):
        return ""
    if not _same_integer(
        properties.get(PREVIEW_REVISION),
        token.get("revision"),
    ):
        return ""
    return path
