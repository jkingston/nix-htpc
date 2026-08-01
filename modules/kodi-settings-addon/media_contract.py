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
CURRENT_VIEW_SLOT_FIELDS = (
    "targetvalid",
    "targetfill",
    "targetmarker",
    "time",
    "delta",
    "prompt",
    "previewstatus",
    "previewpath",
    "previewanchor",
)
CURRENT_SEEK_CONTROLLER_PROPERTY_KEYS = (
    "active",
    "generation",
    "targetseconds",
    "modal",
)
CURRENT_SEEK_VIEW_PROPERTY_KEYS = (
    "actualmarker",
    "viewactive",
    "viewslot",
) + tuple(
    "%s.%s" % (slot, field)
    for slot in ("a", "b")
    for field in CURRENT_VIEW_SLOT_FIELDS
)
CURRENT_SEEK_PROPERTY_KEYS = (
    CURRENT_SEEK_CONTROLLER_PROPERTY_KEYS + CURRENT_SEEK_VIEW_PROPERTY_KEYS
)

# Jellyfin lane chapter contract. The producer publishes the sanitized manifest
# first, its commit token next, and AVAILABLE last.
CHAPTERS_AVAILABLE = "jellyfin.htpc.chapters.available"
CHAPTERS_MANIFEST = "jellyfin.htpc.chapters.manifest"
CHAPTERS_TOKEN = "jellyfin.htpc.chapters.token"
CHAPTERS_PLAYBACK = "jellyfin.htpc.chapters.playback"
CHAPTERS_REVISION = "jellyfin.htpc.chapters.revision"
CHAPTER_CONTRACT_VERSION = 1

# Preview requests and responses each use one JSON property as their atomic
# commit point. Chapter images are never valid for this namespace.
PREVIEW_CONTRACT = "jellyfin.htpc.preview.v2"
PREVIEW_CONTRACT_VERSION = 2
SEEK_REQUEST = "htpc.seek.request.v1"


def _text(value):
    if value is None:
        return ""
    return str(value)


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


def preview_validation(properties, seek_snapshot):
    """Return ``(path, reason)`` for the exact-preview contract.

    Reason codes are deliberately value-free so they can be logged without
    exposing a server, token, item id, or generated filesystem path.
    """
    if not seek_snapshot.get("active"):
        return "", "idle"
    serialized = properties.get(PREVIEW_CONTRACT) or ""
    if not serialized:
        return "", "producer-empty"
    try:
        token = json.loads(serialized)
    except (TypeError, ValueError):
        return "", "token-json"
    if not isinstance(token, dict) or token.get("schema") != PREVIEW_CONTRACT_VERSION:
        return "", "token-schema"
    if token.get("status") != "ready":
        return "", _text(token.get("status") or "producer-empty")
    path = _text(token.get("path")).strip()
    if not path:
        return "", "producer-empty"
    try:
        generation = int(token.get("seek_generation"))
        target = int(token.get("target_seconds"))
        frame = int(token.get("frame_index"))
        revision = int(token.get("revision"))
        float(token.get("sample_seconds"))
    except (TypeError, ValueError):
        return "", "token-fields"
    if generation != int(seek_snapshot.get("generation", -1)):
        return "", "generation"
    if target != int(seek_snapshot.get("target_seconds", -1)):
        return "", "target"
    if frame < 0 or revision < 0 or not _text(token.get("playback")):
        return "", "token-fields"
    expected_consumer = _text(seek_snapshot.get("consumer_nonce"))
    if not expected_consumer or _text(
        token.get("consumer_nonce")
    ) != expected_consumer:
        return "", "consumer"
    expected_epoch = _text(seek_snapshot.get("playback_epoch"))
    if not expected_epoch or _text(token.get("playback_epoch")) != expected_epoch:
        return "", "playback-epoch"
    return path, "ready"


def preview_status(properties, seek_snapshot):
    try:
        contract = json.loads(properties.get(PREVIEW_CONTRACT) or "")
    except (TypeError, ValueError):
        return "none"
    if (
        not isinstance(contract, dict)
        or contract.get("schema") != PREVIEW_CONTRACT_VERSION
    ):
        return "none"
    status = _text(contract.get("status"))
    if status not in (
        "initialising",
        "warming",
        "ready",
        "temporarily-failed",
        "unavailable",
    ):
        return "none"
    expected_consumer = _text(seek_snapshot.get("consumer_nonce"))
    if not expected_consumer or _text(
        contract.get("consumer_nonce")
    ) != expected_consumer:
        return "none"
    expected_epoch = _text(seek_snapshot.get("playback_epoch"))
    if not expected_epoch or _text(
        contract.get("playback_epoch")
    ) != expected_epoch:
        return "none"
    return status


def validated_preview(properties, seek_snapshot):
    """Return an exact preview path only after the JSON commit token matches."""
    return preview_validation(properties, seek_snapshot)[0]
