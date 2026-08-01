# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

import hashlib
import io
import json
import math
import os
import shutil
import threading
import time
from collections import OrderedDict

import requests
import xbmc

from .helper import LazyLogger, window
from .helper.utils import translate_path


LOG = LazyLogger(__name__)

# Exact-preview component properties consumed by the settings service.
PREVIEW_PATH = "jellyfin.htpc.seekpreview"
PREVIEW_TARGET = "jellyfin.htpc.seekpreviewtarget"

# Versioned exact-preview contract. PREVIEW_TOKEN is the commit marker.
PREVIEW_TOKEN = "jellyfin.htpc.seekpreviewtoken"
PREVIEW_PLAYBACK = "jellyfin.htpc.seekpreviewplayback"
PREVIEW_GENERATION = "jellyfin.htpc.seekpreviewgeneration"
PREVIEW_SAMPLE = "jellyfin.htpc.seekpreviewsample"
PREVIEW_FRAME = "jellyfin.htpc.seekpreviewframe"
PREVIEW_REVISION = "jellyfin.htpc.seekpreviewrevision"

# Versioned chapter contract. Chapter images never satisfy exact preview jobs.
CHAPTER_AVAILABLE = "jellyfin.htpc.chapters.available"
CHAPTER_MANIFEST = "jellyfin.htpc.chapters.manifest"
CHAPTER_TOKEN = "jellyfin.htpc.chapters.token"
CHAPTER_PLAYBACK = "jellyfin.htpc.chapters.playback"
CHAPTER_REVISION = "jellyfin.htpc.chapters.revision"

SEEK_ACTIVE = "htpc.seek.active"
SEEK_GENERATION = "htpc.seek.generation"
SEEK_TARGET = "htpc.seek.targetseconds"

PREVIEW_SCHEMA = 1
CHAPTER_SCHEMA = 1
SEEK_POLL_SECONDS = 0.05
SPRITE_CACHE_BYTES = 16 * 1024 * 1024
DECODED_SPRITE_LIMIT = 1
FRAME_CACHE_LIMIT = 48
CHAPTER_CACHE_LIMIT = 24
MAX_CHAPTERS = CHAPTER_CACHE_LIMIT
CHAPTER_MIN_SEPARATION_SECONDS = 1.0
PREVIEW_RETRY_BACKOFFS = (0.10, 0.30)
PREVIEW_STATIONARY_RETRY_SECONDS = 1.0
PREVIEW_DIAGNOSTIC_INTERVAL_SECONDS = 30.0
# The final delay repeats indefinitely. Playback metadata is expected to
# recover after transient server/network outages without restarting playback.
METADATA_TRANSIENT_RETRY_DELAYS = (0.25, 1.0, 3.0, 5.0)
METADATA_DIAGNOSTIC_INTERVAL_SECONDS = 30.0
METADATA_TERMINAL_HTTP_STATUSES = frozenset((400, 401, 403, 404))
CHAPTER_RETRY_BACKOFFS = (0.20, 0.60)


class PreviewFailure(Exception):
    def __init__(
        self,
        message,
        transient=True,
        stage="producer",
        reason="unavailable",
    ):
        super(PreviewFailure, self).__init__(message)
        self.transient = bool(transient)
        self.stage = str(stage)
        self.reason = str(reason)


class ByteLruCache(object):
    """A thread-safe, strictly byte-bounded LRU."""

    def __init__(self, byte_limit):
        self.byte_limit = max(0, int(byte_limit))
        self._items = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()

    @property
    def byte_size(self):
        with self._lock:
            return self._bytes

    def __len__(self):
        with self._lock:
            return len(self._items)

    def keys(self):
        with self._lock:
            return list(self._items)

    def get(self, key):
        with self._lock:
            value = self._items.pop(key, None)
            if value is not None:
                self._items[key] = value
            return value

    def put(self, key, value):
        value_size = len(value)
        with self._lock:
            previous = self._items.pop(key, None)
            if previous is not None:
                self._bytes -= len(previous)

            if value_size > self.byte_limit:
                return False

            while self._items and self._bytes + value_size > self.byte_limit:
                _, evicted = self._items.popitem(last=False)
                self._bytes -= len(evicted)

            self._items[key] = value
            self._bytes += value_size
            return True

    def remove(self, key):
        with self._lock:
            value = self._items.pop(key, None)
            if value is None:
                return False
            self._bytes -= len(value)
            return True


class FileLruCache(object):
    """A count-bounded LRU of immutable generated files."""

    def __init__(self, entry_limit):
        self.entry_limit = max(0, int(entry_limit))
        self._items = OrderedDict()
        self._lock = threading.RLock()

    def __len__(self):
        with self._lock:
            return len(self._items)

    def get(self, key):
        with self._lock:
            path = self._items.pop(key, None)
            if path is None:
                return None
            if not os.path.exists(path):
                return None
            self._items[key] = path
            return path

    def put(self, key, path):
        evicted = []
        with self._lock:
            previous = self._items.pop(key, None)
            if previous is not None and previous != path:
                evicted.append(previous)
            self._items[key] = path
            while len(self._items) > self.entry_limit:
                _, old_path = self._items.popitem(last=False)
                if old_path != path:
                    evicted.append(old_path)

        for old_path in evicted:
            try:
                os.unlink(old_path)
            except OSError:
                pass

    def items(self):
        with self._lock:
            return list(self._items.items())


class OutputSlots(object):
    """Two atomic output files; the active path is never replaced."""

    def __init__(self, root, revision):
        os.makedirs(root, exist_ok=True)
        self.paths = (
            os.path.join(root, "preview-a-r%d.jpg" % int(revision)),
            os.path.join(root, "preview-b-r%d.jpg" % int(revision)),
        )
        self.active_path = None
        self._lock = threading.RLock()

    def stage(self, source_path):
        with self._lock:
            destination = (
                self.paths[1] if self.active_path == self.paths[0] else self.paths[0]
            )
            temporary = "%s.tmp-%s" % (
                destination,
                threading.current_thread().ident or 0,
            )
            shutil.copyfile(source_path, temporary)
            os.replace(temporary, destination)
            return destination

    def activate(self, path):
        with self._lock:
            if path not in self.paths:
                raise ValueError("output path is not a managed slot")
            self.active_path = path

    def clear(self):
        with self._lock:
            self.active_path = None


class LatestRequestSlot(object):
    """One replaceable job plus the newest metadata for its work identity."""

    def __init__(self):
        self._condition = threading.Condition(threading.RLock())
        self._pending = None
        self._latest = None
        self._latest_key = None
        self._closed = False

    @property
    def pending_count(self):
        with self._condition:
            return 1 if self._pending is not None else 0

    def submit(self, request):
        with self._condition:
            if self._closed:
                return False
            self._latest = request
            self._latest_key = request["key"]
            self._pending = request
            self._condition.notify_all()
            return True

    def clear(self):
        with self._condition:
            self._latest = None
            self._latest_key = None
            self._pending = None
            self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self._latest = None
            self._latest_key = None
            self._pending = None
            self._condition.notify_all()

    def is_latest(self, key):
        with self._condition:
            return not self._closed and self._latest_key == key

    def latest_for(self, key):
        """Return the newest request for one still-current work identity."""
        with self._condition:
            if self._closed or self._latest_key != key:
                return None
            return self._latest

    def discard_pending(self, request):
        """Drop only the pending request already satisfied by publication."""
        with self._condition:
            if self._pending is not request:
                return False
            self._pending = None
            return True

    def take(self, abort):
        with self._condition:
            while (
                self._pending is None
                and not self._closed
                and not abort.is_set()
            ):
                self._condition.wait(0.20)
            if self._closed or abort.is_set():
                return None
            request = self._pending
            self._pending = None
            return request

    def wait_retry(self, key, delay, abort):
        deadline = time.monotonic() + max(0.0, float(delay))
        with self._condition:
            while (
                not self._closed
                and not abort.is_set()
                and self._latest_key == key
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            return (
                not self._closed
                and not abort.is_set()
                and self._latest_key == key
            )

    def retry_after(self, request, delay, abort):
        """Requeue one unchanged request after an interruptible cooldown.

        A newer request is already pending when it supersedes this object, so
        never replace it here. Polling the abort event bounds shutdown latency
        even though ``threading.Event.set()`` cannot wake this condition.
        """
        deadline = time.monotonic() + max(0.0, float(delay))
        with self._condition:
            while (
                not self._closed
                and not abort.is_set()
                and self._latest is request
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(min(remaining, SEEK_POLL_SECONDS))

            if (
                self._closed
                or abort.is_set()
                or self._latest is not request
            ):
                return False
            if self._pending is None:
                self._pending = request
                self._condition.notify_all()
            return True


def parse_time_label(value):
    """Convert a Kodi time label to seconds."""
    if not value:
        return None
    try:
        parts = [int(part) for part in value.strip().split(":")]
    except (AttributeError, TypeError, ValueError):
        return None
    if not 1 <= len(parts) <= 3:
        return None
    seconds = 0
    for part in parts:
        seconds = (seconds * 60) + part
    return seconds


def format_time(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, seconds)
    return "%d:%02d" % (minutes, seconds)


def frame_for_time(seconds, info):
    interval = max(1, int(info["Interval"]))
    count = max(1, int(info["ThumbnailCount"]))
    return min(max(0, int(float(seconds) * 1000) // interval), count - 1)


def sample_seconds_for_frame(frame, info):
    return (int(frame) * max(1, int(info["Interval"]))) / 1000.0


def tile_for_frame(frame, info):
    """Return the frame, sprite and crop box for a Jellyfin trickplay frame."""
    count = max(1, int(info["ThumbnailCount"]))
    columns = max(1, int(info["TileWidth"]))
    rows = max(1, int(info["TileHeight"]))
    width = max(1, int(info["Width"]))
    height = max(1, int(info["Height"]))

    frame = min(max(0, int(frame)), count - 1)
    per_sprite = columns * rows
    sprite, within = divmod(frame, per_sprite)
    row, column = divmod(within, columns)
    box = (
        column * width,
        row * height,
        (column + 1) * width,
        (row + 1) * height,
    )
    return frame, sprite, box


def tile_for_time(seconds, info):
    return tile_for_frame(frame_for_time(seconds, info), info)


TRICKPLAY_INFO_FIELDS = (
    "Interval",
    "ThumbnailCount",
    "TileWidth",
    "TileHeight",
    "Width",
    "Height",
)


def _positive_integer(value):
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        not math.isfinite(numeric)
        or numeric <= 0
        or not numeric.is_integer()
    ):
        return None
    return int(numeric)


def _validated_trickplay_info(info):
    if not isinstance(info, dict):
        return None
    normalized = dict(info)
    for field in TRICKPLAY_INFO_FIELDS:
        value = _positive_integer(info.get(field))
        if value is None:
            return None
        normalized[field] = value
    return normalized


def _resolution_map(mapping):
    """Return a complete, structurally valid width -> info mapping."""
    if not isinstance(mapping, dict) or not mapping:
        return None
    resolutions = []
    for raw_width, raw_info in mapping.items():
        width = _positive_integer(raw_width)
        info = _validated_trickplay_info(raw_info)
        if width is None or info is None or info["Width"] != width:
            return None
        resolutions.append((width, info))
    return resolutions


def _looks_like_trickplay_info(value):
    return isinstance(value, dict) and any(
        field in value for field in TRICKPLAY_INFO_FIELDS
    )


def select_trickplay(trickplay, media_source_id, preferred_width=320):
    """Return the selected media source, width and TrickplayInfo.

    Jellyfin manifests are normally keyed by media-source id. Playback can
    carry a stale or transcoded source id, so a fallback manifest must also
    replace the id sent to the tile endpoint.
    """
    if not isinstance(trickplay, dict) or not trickplay:
        return None, None, None

    flattened = _resolution_map(trickplay)
    if flattened is not None:
        width, info = min(
            flattened,
            key=lambda value: abs(value[0] - preferred_width),
        )
        return media_source_id, width, info

    # A mixture of direct TrickplayInfo entries and nested sources is
    # ambiguous. Do not reinterpret the direct entries as malformed sources.
    if any(_looks_like_trickplay_info(value) for value in trickplay.values()):
        return None, None, None

    exact_key = next(
        (
            source_id
            for source_id in trickplay
            if media_source_id is not None
            and str(source_id) == str(media_source_id)
        ),
        None,
    )
    if exact_key is not None:
        exact = _resolution_map(trickplay.get(exact_key))
        if exact is None:
            return None, None, None
        width, info = min(
            exact,
            key=lambda value: abs(value[0] - preferred_width),
        )
        return exact_key, width, info

    valid_sources = []
    for source_id, source in trickplay.items():
        resolutions = _resolution_map(source)
        if resolutions is not None:
            valid_sources.append((source_id, resolutions))
    if len(valid_sources) != 1:
        return None, None, None

    selected_source_id, resolutions = valid_sources[0]
    width, info = min(
        resolutions,
        key=lambda value: abs(value[0] - preferred_width),
    )
    return selected_source_id, width, info


def _safe_label(value, fallback):
    try:
        value = str(value or "")
    except Exception:
        value = ""
    value = "".join(
        character
        if ord(character) >= 32 and ord(character) != 127
        else " "
        for character in value
    )
    value = " ".join(value.split())
    return (value or fallback)[:160]


def _clean_number(value):
    value = float(value)
    if value.is_integer():
        return int(value)
    return round(value, 3)


def _property_true(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _http_status(error):
    response = getattr(error, "response", None)
    candidates = (
        getattr(response, "status_code", None),
        getattr(error, "status_code", None),
        getattr(error, "status", None),
    )
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _request_failure_reason(error):
    status = _http_status(error)
    if status is not None:
        return "http-%d" % status
    timeout_type = getattr(requests, "Timeout", ())
    if timeout_type and isinstance(error, timeout_type):
        return "timeout"
    connection_type = getattr(requests, "ConnectionError", ())
    if connection_type and isinstance(error, connection_type):
        return "connection"
    return "request-error"


def _media_kind(value):
    """Return a small diagnostic category, never an arbitrary metadata value."""
    kind = str(value or "")
    return kind if kind in ("Movie", "Episode", "Video") else "unknown"


def _log_stage_once(state, stage, outcome, reason):
    diagnostic = (str(stage), str(outcome), str(reason))
    lock = state.setdefault("diagnostics_lock", threading.Lock())
    with lock:
        seen = state.setdefault("diagnostics_seen", set())
        if diagnostic in seen:
            return False
        seen.add(diagnostic)
    try:
        LOG.info(
            "HTPC trickplay stage=%s outcome=%s reason=%s",
            diagnostic[0],
            diagnostic[1],
            diagnostic[2],
        )
    except Exception:
        # Diagnostics must never turn a valid preview into a failed request.
        pass
    return True


def sanitize_chapters(chapters, duration_seconds):
    """Return finite, in-range, sorted and meaningfully distinct chapters."""
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError):
        return []
    if not math.isfinite(duration) or duration <= 0:
        return []

    candidates = []
    for index, chapter in enumerate(chapters or []):
        if not isinstance(chapter, dict):
            continue
        try:
            seconds = float(chapter.get("StartPositionTicks")) / 10000000.0
        except (TypeError, ValueError):
            continue
        if not math.isfinite(seconds) or seconds < 0 or seconds >= duration:
            continue
        candidates.append(
            {
                "kind": "chapter",
                "id": "chapter-%04d" % index,
                "index": index,
                "time_seconds": _clean_number(seconds),
                "label": _safe_label(
                    chapter.get("Name"),
                    "Chapter %d" % (index + 1),
                ),
                "image": "",
            }
        )

    candidates.sort(key=lambda entry: (float(entry["time_seconds"]), entry["index"]))
    sanitized = []
    for entry in candidates:
        if sanitized and (
            float(entry["time_seconds"])
            - float(sanitized[-1]["time_seconds"])
            < CHAPTER_MIN_SEPARATION_SECONDS
        ):
            continue
        sanitized.append(entry)
        if len(sanitized) >= MAX_CHAPTERS:
            break
    return sanitized

def media_duration_seconds(item, metadata, player=None):
    try:
        ticks = float(metadata.get("RunTimeTicks"))
        duration = ticks / 10000000.0
        if math.isfinite(duration) and duration > 0:
            return duration
    except (AttributeError, TypeError, ValueError):
        pass

    try:
        duration = float(item.get("Runtime"))
        if math.isfinite(duration) and duration > 0:
            return duration
    except (AttributeError, TypeError, ValueError):
        pass

    try:
        duration = float(player.getTotalTime())
        if math.isfinite(duration) and duration > 0:
            return duration
    except Exception:
        pass
    return None


def make_playback_token(item, revision):
    identity = "|".join(
        str(item.get(key) or "")
        for key in ("Id", "MediaSourceId", "PlaySessionId")
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return "p%d-%s" % (int(revision), digest)


def make_preview_request(
    playback_token,
    revision,
    seek_generation,
    target_text,
    info,
    direction=0,
):
    target_text = str(target_text).strip()
    target_seconds = float(target_text)
    if not math.isfinite(target_seconds) or target_seconds < 0:
        raise ValueError("invalid exact seek target")

    frame = frame_for_time(target_seconds, info)
    sample_seconds = sample_seconds_for_frame(frame, info)
    payload = {
        "schema": PREVIEW_SCHEMA,
        "playback": playback_token,
        "seek_generation": str(seek_generation),
        "target_seconds": _clean_number(target_seconds),
        "sample_seconds": _clean_number(sample_seconds),
        "frame_index": int(frame),
        "revision": int(revision),
    }
    # Cursor targets sharing a sampled frame are one decode job. The complete
    # target stays in the token and request so publication can still commit the
    # exact newest cursor position.
    key = (
        payload["playback"],
        payload["revision"],
        payload["seek_generation"],
        payload["sample_seconds"],
        payload["frame_index"],
    )
    return {
        "key": key,
        "token": payload,
        "target_text": target_text,
        "target_seconds": target_seconds,
        "frame": frame,
        "direction": -1 if direction < 0 else 1 if direction > 0 else 0,
    }


class TrickplayPreviewManager(object):
    """Latest-target-wins exact previews plus an independent chapter contract."""

    def __init__(self, player):
        self.player = player
        self.abort = None
        self.thread = None
        self.cache_root = None
        self._revision = 0
        self._lifecycle_lock = threading.RLock()
        self._property_lock = threading.RLock()

    def start(self, item):
        self.stop()
        if item.get("Type") not in ("Movie", "Episode", "Video"):
            return

        with self._lifecycle_lock:
            self._revision += 1
            revision = self._revision
        playback_token = make_playback_token(item, revision)
        safe_item = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in str(item.get("Id") or "unknown")
        )[:80]
        cache_root = os.path.join(
            translate_path("special://temp"),
            "jellyfin-trickplay",
            safe_item,
            playback_token,
        )
        abort = threading.Event()
        thread = threading.Thread(
            target=self._run,
            args=(item, revision, playback_token, abort, cache_root),
            name="jellyfin-preview-lifecycle",
        )
        thread.daemon = True
        self.abort = abort
        self.thread = thread
        self.cache_root = cache_root
        thread.start()

    def stop(self):
        abort = self.abort
        thread = self.thread
        self.abort = None
        self.thread = None
        self.cache_root = None
        if abort is not None:
            abort.set()
        self._clear_all_properties()
        if thread is not None and thread.is_alive():
            thread.join(1.0)

    def _run(self, item, revision, playback_token, abort, cache_root):
        state = None
        try:
            os.makedirs(cache_root, exist_ok=True)
            client = item["Server"]
            metadata = self._load_metadata(item, abort)
            if metadata is None:
                return
            media_source_id, width, info = select_trickplay(
                metadata.get("Trickplay"),
                item.get("MediaSourceId"),
            )
            if info is None:
                reason = (
                    "no-manifest"
                    if not metadata.get("Trickplay")
                    else "unsupported-manifest"
                )
                LOG.info(
                    "HTPC trickplay stage=metadata outcome=unavailable "
                    "reason=%s media=%s",
                    reason,
                    _media_kind(item.get("Type")),
                )
            else:
                LOG.info(
                    "HTPC trickplay stage=metadata outcome=ready "
                    "reason=manifest media=%s",
                    _media_kind(item.get("Type")),
                )
            duration = media_duration_seconds(item, metadata, self.player)
            chapters = sanitize_chapters(metadata.get("Chapters") or [], duration)
            state = self._new_state(
                item,
                client,
                media_source_id,
                revision,
                playback_token,
                cache_root,
                width,
                info,
                duration,
                chapters,
            )
            state["abort"] = abort
            if abort.is_set():
                return
            self._publish_chapter_manifest(state)

            workers = [
                threading.Thread(
                    target=self._preview_worker,
                    args=(state, abort),
                    name="jellyfin-preview-foreground",
                ),
                threading.Thread(
                    target=self._prefetch_worker,
                    args=(state, abort),
                    name="jellyfin-preview-neighbor",
                ),
            ]
            if len(chapters) >= 2:
                workers.append(
                    threading.Thread(
                        target=self._chapter_worker,
                        args=(state, abort),
                        name="jellyfin-chapter-images",
                    )
                )
            for worker in workers:
                worker.daemon = True
                worker.start()
            state["workers"] = workers

            last_observed = None
            last_target = None
            while not abort.wait(SEEK_POLL_SECONDS):
                if not _property_true(window(SEEK_ACTIVE)):
                    if last_observed is not None:
                        state["request_slot"].clear()
                        state["prefetch_slot"].clear()
                        self._clear_preview_properties(state)
                    last_observed = None
                    last_target = None
                    continue

                generation = window(SEEK_GENERATION)
                target_text = window(SEEK_TARGET)
                observed = (generation, target_text)
                if not generation or not target_text or observed == last_observed:
                    continue
                try:
                    numeric_target = float(target_text)
                    direction = (
                        1
                        if last_target is not None and numeric_target > last_target
                        else -1
                        if last_target is not None and numeric_target < last_target
                        else 0
                    )
                    if info is None:
                        raise ValueError("no exact trickplay metadata")
                    request = make_preview_request(
                        playback_token,
                        revision,
                        generation,
                        target_text,
                        info,
                        direction,
                    )
                except (TypeError, ValueError):
                    state["request_slot"].clear()
                    self._clear_preview_properties(state)
                    last_observed = observed
                    continue

                state["request_slot"].submit(request)
                _log_stage_once(
                    state,
                    "request",
                    "ready",
                    "seek-target",
                )
                last_observed = observed
                last_target = numeric_target
        except Exception:
            if not abort.is_set():
                LOG.warning(
                    "HTPC trickplay stage=lifecycle outcome=failed "
                    "reason=unexpected"
                )
        finally:
            abort.set()
            if state is not None:
                self._close_state(state)
                self._clear_if_owned(state)
            shutil.rmtree(cache_root, ignore_errors=True)

    @staticmethod
    def _load_metadata(item, abort, clock=None):
        """Fetch item metadata until it succeeds or playback is aborted.

        This runs on the preview lifecycle thread, never Kodi's player thread.
        Known terminal HTTP responses stop immediately. Other failures retry
        indefinitely with a capped delay and periodic diagnostics. Event.wait
        makes shutdown immediate even during the longest delay.
        """
        clock = clock or time.monotonic
        failures = 0
        last_diagnostic = None
        while not abort.is_set():
            try:
                metadata = item["Server"].jellyfin.get_item(item["Id"])
                if not isinstance(metadata, dict) or not metadata:
                    raise ValueError("item metadata was not an object")
                if abort.is_set():
                    return None
                if failures:
                    LOG.info(
                        "HTPC trickplay stage=metadata outcome=recovered "
                        "reason=retry attempts=%s",
                        failures + 1,
                    )
                return metadata
            except Exception as error:
                if abort.is_set():
                    return None
                failures += 1
                status = _http_status(error)
                if status in METADATA_TERMINAL_HTTP_STATUSES:
                    LOG.warning(
                        "HTPC trickplay stage=metadata outcome=unavailable "
                        "reason=http-%s",
                        status,
                    )
                    return None

                if METADATA_TRANSIENT_RETRY_DELAYS:
                    index = min(
                        failures - 1,
                        len(METADATA_TRANSIENT_RETRY_DELAYS) - 1,
                    )
                    delay = METADATA_TRANSIENT_RETRY_DELAYS[index]
                else:
                    delay = SEEK_POLL_SECONDS
                now = clock()
                if (
                    last_diagnostic is None
                    or now - last_diagnostic
                    >= METADATA_DIAGNOSTIC_INTERVAL_SECONDS
                ):
                    LOG.warning(
                        "HTPC trickplay stage=metadata outcome=retry "
                        "reason=%s attempt=%s retry_seconds=%s",
                        _request_failure_reason(error),
                        failures,
                        delay,
                    )
                    last_diagnostic = now
                if abort.wait(max(0.0, float(delay))):
                    return None
        return None

    def _new_state(
        self,
        item,
        client,
        media_source_id,
        revision,
        playback_token,
        cache_root,
        width,
        info,
        duration,
        chapters,
    ):
        frame_root = os.path.join(cache_root, "frames")
        chapter_root = os.path.join(cache_root, "chapters")
        output_root = os.path.join(cache_root, "output")
        for path in (frame_root, chapter_root, output_root):
            os.makedirs(path, exist_ok=True)
        return {
            "item_id": item["Id"],
            "media_source_id": media_source_id,
            "client": client,
            "width": width,
            "info": info,
            "duration": duration,
            "chapters": chapters,
            "revision": int(revision),
            "playback_token": playback_token,
            "frame_root": frame_root,
            "chapter_root": chapter_root,
            "sprite_cache": ByteLruCache(SPRITE_CACHE_BYTES),
            "frame_cache": FileLruCache(FRAME_CACHE_LIMIT),
            "chapter_cache": FileLruCache(CHAPTER_CACHE_LIMIT),
            "sprite_condition": threading.Condition(threading.RLock()),
            "inflight_sprites": set(),
            "background_network_lock": threading.Lock(),
            "decoded_sprites": OrderedDict(),
            "decode_lock": threading.RLock(),
            "chapter_lock": threading.RLock(),
            "request_slot": LatestRequestSlot(),
            "prefetch_slot": LatestRequestSlot(),
            "output_slots": OutputSlots(output_root, revision),
            "exact_session": self._new_session(client),
            "prefetch_session": self._new_session(client),
            "chapter_session": self._new_session(client),
            "manifest_revision": 0,
            "preview_failure_diagnostics": {},
            "diagnostics_lock": threading.Lock(),
            "diagnostics_seen": set(),
            "workers": [],
        }

    def _preview_worker(self, state, abort):
        while not abort.is_set():
            request = state["request_slot"].take(abort)
            if request is None:
                return
            try:
                self._process_preview_request(state, request, abort)
            except Exception as error:
                if not abort.is_set():
                    LOG.warning(
                        "HTPC trickplay stage=producer outcome=failed "
                        "reason=unexpected"
                    )

    def _process_preview_request(self, state, request, abort):
        attempts = len(PREVIEW_RETRY_BACKOFFS) + 1
        for attempt in range(attempts):
            if self._latest_current_request(state, request, abort) is None:
                return False
            try:
                source_path = self._resolve_frame_path(
                    state,
                    request["frame"],
                    abort,
                    foreground=True,
                )
                if self._latest_current_request(state, request, abort) is None:
                    return False
                published = self._publish_preview(
                    state,
                    request,
                    source_path,
                    abort,
                )
            except PreviewFailure as error:
                retry = error.transient and attempt < len(PREVIEW_RETRY_BACKOFFS)
                if not retry:
                    current = self._clear_preview_if_current(
                        state,
                        request,
                        abort,
                    )
                    if current:
                        self._log_preview_failure(state, request, error)
                    if current and error.transient:
                        state["request_slot"].retry_after(
                            request,
                            PREVIEW_STATIONARY_RETRY_SECONDS,
                            abort,
                        )
                    return False
                if not state["request_slot"].wait_retry(
                    request["key"],
                    PREVIEW_RETRY_BACKOFFS[attempt],
                    abort,
                ):
                    return False
                continue

            if published is None:
                return False
            state["request_slot"].discard_pending(published)
            self._queue_one_neighbor(state, published)
            return True
        return False

    @staticmethod
    def _log_preview_failure(state, request, error):
        now = time.monotonic()
        diagnostic = (error.stage, error.reason)
        diagnostics = state.setdefault("preview_failure_diagnostics", {})
        previous = diagnostics.get(diagnostic)
        if (
            previous is not None
            and now - previous < PREVIEW_DIAGNOSTIC_INTERVAL_SECONDS
        ):
            return False
        diagnostics[diagnostic] = now
        LOG.warning(
            "HTPC trickplay stage=%s outcome=unavailable reason=%s "
            "transient=%s",
            error.stage,
            error.reason,
            error.transient,
        )
        return True

    def _latest_current_request(self, state, request, abort):
        """Rebind same-frame work to the exact newest cursor target."""
        if (
            abort.is_set()
            or request["token"]["playback"] != state["playback_token"]
            or request["token"]["revision"] != state["revision"]
        ):
            return None

        latest = state["request_slot"].latest_for(request["key"])
        if latest is None:
            return None
        token = latest["token"]
        if (
            token["playback"] != state["playback_token"]
            or token["revision"] != state["revision"]
            or latest["frame"] != request["frame"]
            or not _property_true(window(SEEK_ACTIVE))
            or window(SEEK_GENERATION) != token["seek_generation"]
            or window(SEEK_TARGET) != latest["target_text"]
        ):
            return None
        return latest

    def _publish_preview(self, state, request, source_path, abort):
        """Commit this sampled frame for the exact latest matching target."""
        try:
            staged_path = state["output_slots"].stage(source_path)
        except Exception:
            raise PreviewFailure(
                "preview publication failed",
                True,
                stage="publication",
                reason="file-io",
            )
        try:
            with self._property_lock:
                latest = self._latest_current_request(state, request, abort)
                if latest is None:
                    return None
                payload = latest["token"]
                token_json = json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                # Install path and component fields first. The token and exact
                # target are the final consistency fields validated by readers.
                window(PREVIEW_PATH, staged_path)
                state["output_slots"].activate(staged_path)
                window(PREVIEW_PLAYBACK, payload["playback"])
                window(PREVIEW_GENERATION, payload["seek_generation"])
                window(PREVIEW_SAMPLE, str(payload["sample_seconds"]))
                window(PREVIEW_FRAME, str(payload["frame_index"]))
                window(PREVIEW_REVISION, str(payload["revision"]))
                window(PREVIEW_TOKEN, token_json)
                window(PREVIEW_TARGET, latest["target_text"])
                _log_stage_once(
                    state,
                    "publication",
                    "ready",
                    "contract",
                )
                return latest
        except PreviewFailure:
            raise
        except Exception:
            raise PreviewFailure(
                "preview property publication failed",
                True,
                stage="publication",
                reason="property-write",
            )

    def _clear_preview_if_current(self, state, request, abort):
        with self._property_lock:
            if self._latest_current_request(state, request, abort) is None:
                return False
            self._clear_preview_properties(state)
            # A newer target can share this sampled frame while the failed
            # request is in flight. Only the failed request itself is owned
            # here; leave a newer pending version for the worker to retry.
            state["request_slot"].discard_pending(request)
            return True

    def _queue_one_neighbor(self, state, request):
        info = state["info"]
        if info is None:
            return
        direction = request["direction"] or 1
        neighbor = request["frame"] + direction
        if not 0 <= neighbor < int(info["ThumbnailCount"]):
            return
        prefetch = {
            "key": (
                state["playback_token"],
                state["revision"],
                int(neighbor),
            ),
            "frame": int(neighbor),
        }
        state["prefetch_slot"].submit(prefetch)

    def _prefetch_worker(self, state, abort):
        while not abort.is_set():
            request = state["prefetch_slot"].take(abort)
            if request is None:
                return
            try:
                self._resolve_frame_path(
                    state,
                    request["frame"],
                    abort,
                    foreground=False,
                )
            except PreviewFailure as error:
                if not abort.is_set():
                    LOG.debug(
                        "HTPC trickplay stage=%s outcome=unavailable "
                        "reason=%s lane=neighbor",
                        error.stage,
                        error.reason,
                    )

    def _resolve_frame_path(self, state, frame, abort, foreground):
        cached = state["frame_cache"].get(int(frame))
        if cached is not None:
            return cached
        if state["info"] is None:
            raise PreviewFailure(
                "exact trickplay metadata is unavailable",
                False,
                stage="metadata",
                reason="no-manifest",
            )
        if abort.is_set():
            raise PreviewFailure(
                "preview aborted",
                False,
                stage="request",
                reason="aborted",
            )

        frame, sprite, box = tile_for_frame(frame, state["info"])
        sprite_data = self._load_sprite_data(
            state,
            sprite,
            abort,
            foreground,
        )

        with state["decode_lock"]:
            cached = state["frame_cache"].get(frame)
            if cached is not None:
                return cached
            image = state["decoded_sprites"].pop(sprite, None)
            temporary = None
            if image is None:
                try:
                    from PIL import Image
                except ImportError:
                    raise PreviewFailure(
                        "Pillow is unavailable",
                        False,
                        stage="decode",
                        reason="dependency",
                    )
                image = None
                try:
                    image = Image.open(io.BytesIO(sprite_data))
                    image.load()
                except Exception:
                    if image is not None:
                        try:
                            image.close()
                        except Exception:
                            pass
                    state["sprite_cache"].remove(sprite)
                    raise PreviewFailure(
                        "sprite decode failed",
                        True,
                        stage="decode",
                        reason="invalid-image",
                    )
                _log_stage_once(state, "decode", "ready", "jpeg")

            state["decoded_sprites"][sprite] = image
            while len(state["decoded_sprites"]) > DECODED_SPRITE_LIMIT:
                _, old_image = state["decoded_sprites"].popitem(last=False)
                old_image.close()

            try:
                path = os.path.join(state["frame_root"], "frame-%06d.jpg" % frame)
                temporary = "%s.tmp-%s" % (
                    path,
                    threading.current_thread().ident or 0,
                )
                cropped = image.crop(box).convert("RGB")
                try:
                    cropped.save(temporary, "JPEG", quality=88)
                finally:
                    cropped.close()
                os.replace(temporary, path)
            except Exception:
                bad_image = state["decoded_sprites"].pop(sprite, None)
                if bad_image is not None:
                    try:
                        bad_image.close()
                    except Exception:
                        pass
                state["sprite_cache"].remove(sprite)
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
                raise PreviewFailure(
                    "frame crop failed",
                    True,
                    stage="crop",
                    reason="file-io",
                )
            _log_stage_once(state, "crop", "ready", "frame")
            state["frame_cache"].put(frame, path)
            return path

    def _load_sprite_data(self, state, sprite, abort, foreground):
        sprite_data = state["sprite_cache"].get(sprite)
        if sprite_data is not None:
            return sprite_data
        if foreground:
            return self._download_sprite_once(
                state,
                sprite,
                abort,
                foreground=True,
            )

        # Neighbor trickplay and chapter art share the sole background network
        # slot. Exact requests remain independent and always have a foreground
        # lane available.
        with state["background_network_lock"]:
            sprite_data = state["sprite_cache"].get(sprite)
            if sprite_data is not None:
                return sprite_data
            return self._download_sprite_once(
                state,
                sprite,
                abort,
                foreground=False,
            )

    def _download_sprite_once(self, state, sprite, abort, foreground):
        condition = state["sprite_condition"]
        with condition:
            sprite_data = state["sprite_cache"].get(sprite)
            while (
                sprite_data is None
                and sprite in state["inflight_sprites"]
                and not abort.is_set()
            ):
                condition.wait(SEEK_POLL_SECONDS)
                sprite_data = state["sprite_cache"].get(sprite)
            if sprite_data is not None:
                return sprite_data
            if abort.is_set():
                raise PreviewFailure(
                    "preview aborted",
                    False,
                    stage="download",
                    reason="aborted",
                )
            state["inflight_sprites"].add(sprite)

        try:
            session = (
                state["exact_session"] if foreground else state["prefetch_session"]
            )
            handler = "Videos/%s/Trickplay/%s/%s.jpg" % (
                state["item_id"],
                state["width"],
                sprite,
            )
            try:
                sprite_data = self._download(
                    state["client"],
                    handler,
                    {"MediaSourceId": state["media_source_id"]},
                    session=session,
                )
            except Exception as error:
                raise PreviewFailure(
                    "sprite download failed",
                    self._is_transient_download_error(error),
                    stage="download",
                    reason=_request_failure_reason(error),
                )
            if not isinstance(sprite_data, (bytes, bytearray)) or not sprite_data:
                raise PreviewFailure(
                    "sprite response was empty",
                    True,
                    stage="download",
                    reason="empty-response",
                )
            if abort.is_set():
                raise PreviewFailure(
                    "preview aborted",
                    False,
                    stage="download",
                    reason="aborted",
                )
            state["sprite_cache"].put(sprite, sprite_data)
            _log_stage_once(state, "download", "ready", "sprite")
            return sprite_data
        finally:
            with condition:
                state["inflight_sprites"].discard(sprite)
                condition.notify_all()

    @staticmethod
    def _is_transient_download_error(error):
        status = _http_status(error)
        if status is None:
            return True
        return status in (408, 425, 429) or status >= 500

    def _chapter_worker(self, state, abort):
        # Chapter artwork has a separate cache, session and publication
        # contract. It is never consulted by _process_preview_request().
        for entry in list(state["chapters"])[:CHAPTER_CACHE_LIMIT]:
            if abort.is_set():
                return
            path = self._download_chapter_image(state, entry, abort)
            if path is None or abort.is_set():
                continue
            with state["chapter_lock"]:
                entry["image"] = path
                state["manifest_revision"] += 1
                self._publish_chapter_manifest(state)

    def _download_chapter_image(self, state, entry, abort):
        cached = state["chapter_cache"].get(entry["id"])
        if cached is not None:
            return cached
        path = os.path.join(state["chapter_root"], "%s.jpg" % entry["id"])
        handler = "Items/%s/Images/Chapter/%s" % (
            state["item_id"],
            entry["index"],
        )
        attempts = len(CHAPTER_RETRY_BACKOFFS) + 1
        for attempt in range(attempts):
            if abort.is_set():
                return None
            try:
                with state["background_network_lock"]:
                    if abort.is_set():
                        return None
                    data = self._download(
                        state["client"],
                        handler,
                        {"MaxWidth": 320, "format": "jpg"},
                        session=state["chapter_session"],
                    )
                if not isinstance(data, (bytes, bytearray)) or not data:
                    raise PreviewFailure(
                        "chapter image %s was empty" % entry["index"],
                        True,
                    )
                temporary = "%s.tmp-%s" % (
                    path,
                    threading.current_thread().ident or 0,
                )
                with open(temporary, "wb") as output:
                    output.write(data)
                os.replace(temporary, path)
                state["chapter_cache"].put(entry["id"], path)
                return path
            except Exception as error:
                transient = self._is_transient_download_error(error)
                if not transient or attempt >= len(CHAPTER_RETRY_BACKOFFS):
                    LOG.debug(
                        "HTPC trickplay stage=chapter-download "
                        "outcome=unavailable reason=%s chapter=%s",
                        _request_failure_reason(error),
                        entry["index"],
                    )
                    return self._chapter_from_trickplay(
                        state,
                        entry,
                        path,
                        abort,
                    )
                if abort.wait(CHAPTER_RETRY_BACKOFFS[attempt]):
                    return None
        return None

    def _chapter_from_trickplay(self, state, entry, path, abort):
        """Materialize an exact chapter-position frame when chapter art fails."""
        if state.get("info") is None or abort.is_set():
            return None
        temporary = None
        try:
            frame = frame_for_time(entry["time_seconds"], state["info"])
            source = self._resolve_frame_path(
                state,
                frame,
                abort,
                foreground=False,
            )
            if abort.is_set():
                return None
            temporary = "%s.tmp-%s" % (
                path,
                threading.current_thread().ident or 0,
            )
            shutil.copyfile(source, temporary)
            os.replace(temporary, path)
            state["chapter_cache"].put(entry["id"], path)
            return path
        except Exception as error:
            reason = getattr(error, "reason", "unexpected")
            LOG.debug(
                "HTPC trickplay stage=chapter-fallback "
                "outcome=unavailable reason=%s chapter=%s",
                reason,
                entry["index"],
            )
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            return None

    def _publish_chapter_manifest(self, state):
        with state["chapter_lock"]:
            abort = state.get("abort")
            if abort is not None and abort.is_set():
                return False
            retained = list(state["chapters"])[:CHAPTER_CACHE_LIMIT]
            entries = [
                dict(entry)
                for entry in retained
                if entry.get("image") and os.path.isfile(entry["image"])
            ]
            # The dialog snapshots its list when it opens. Publish one stable
            # all-retained rail instead of exposing an early two-item subset.
            if len(entries) < 2 or len(entries) != len(retained):
                self._clear_chapter_properties_if_owned_or_empty(state)
                return False
            contract = {
                "schema": CHAPTER_SCHEMA,
                "playback": state["playback_token"],
                "revision": state["revision"],
                "manifest_revision": state["manifest_revision"],
                "expected_count": len(retained),
                "duration_seconds": _clean_number(state["duration"]),
                "entries": entries,
            }
            token = {
                "schema": CHAPTER_SCHEMA,
                "playback": state["playback_token"],
                "revision": state["revision"],
                "manifest_revision": state["manifest_revision"],
            }
            with self._property_lock:
                if abort is not None and abort.is_set():
                    return False
                # AVAILABLE is the read barrier. Drop it during manifest
                # revisions so consumers cannot combine a new manifest with
                # the old token.
                window(CHAPTER_AVAILABLE, clear=True)
                window(CHAPTER_PLAYBACK, state["playback_token"])
                window(CHAPTER_REVISION, str(state["revision"]))
                window(
                    CHAPTER_MANIFEST,
                    json.dumps(contract, sort_keys=True, separators=(",", ":")),
                )
                window(
                    CHAPTER_TOKEN,
                    json.dumps(token, sort_keys=True, separators=(",", ":")),
                )
                window(CHAPTER_AVAILABLE, "true")
        return True

    @staticmethod
    def _new_session(client):
        session = requests.Session()
        session.headers.update(
            {
                "Accept": "image/jpeg",
                "X-Emby-Token": client.config.data["auth.token"],
            }
        )
        return session

    @staticmethod
    def _download(client, handler, params, session=None):
        server = client.config.data["auth.server"].rstrip("/")
        token = client.config.data["auth.token"]
        requester = session if session is not None else requests
        headers = None
        if session is None:
            headers = {
                "Accept": "image/jpeg",
                "X-Emby-Token": token,
            }
        response = requester.get(
            "%s/%s" % (server, handler),
            params={key: value for key, value in params.items() if value is not None},
            headers=headers,
            timeout=(2, 5),
            verify=client.config.data.get("auth.ssl", False),
        )
        response.raise_for_status()
        return response.content

    def _clear_preview_properties(self, state=None):
        with self._property_lock:
            # Clear the final target consistency field first so a stale image
            # is hidden.
            window(PREVIEW_TARGET, clear=True)
            for key in (
                PREVIEW_TOKEN,
                PREVIEW_PATH,
                PREVIEW_PLAYBACK,
                PREVIEW_GENERATION,
                PREVIEW_SAMPLE,
                PREVIEW_FRAME,
                PREVIEW_REVISION,
            ):
                window(key, clear=True)
            if state is not None:
                state["output_slots"].clear()

    def _clear_chapter_properties(self):
        with self._property_lock:
            window(CHAPTER_AVAILABLE, clear=True)
            for key in (
                CHAPTER_TOKEN,
                CHAPTER_MANIFEST,
                CHAPTER_PLAYBACK,
                CHAPTER_REVISION,
            ):
                window(key, clear=True)

    def _clear_chapter_properties_if_owned_or_empty(self, state):
        """Never let a stale lifecycle clear a replacement playback."""
        with self._property_lock:
            owner = window(CHAPTER_PLAYBACK)
            if owner and owner != state["playback_token"]:
                return False
            self._clear_chapter_properties()
            return True

    def _clear_all_properties(self):
        self._clear_preview_properties()
        self._clear_chapter_properties()

    def _clear_if_owned(self, state):
        # Ownership checks and clears are one transaction. A replacement
        # playback may publish immediately after this lock is released, but an
        # old lifecycle can never clear the replacement's properties.
        with self._property_lock:
            if window(PREVIEW_PLAYBACK) == state["playback_token"]:
                self._clear_preview_properties(state)
            if window(CHAPTER_PLAYBACK) == state["playback_token"]:
                self._clear_chapter_properties()

    @staticmethod
    def _close_state(state):
        state["request_slot"].close()
        state["prefetch_slot"].close()
        for worker in state["workers"]:
            if worker.is_alive():
                worker.join(8.0)
        for session_name in (
            "exact_session",
            "prefetch_session",
            "chapter_session",
        ):
            try:
                state[session_name].close()
            except Exception:
                pass
        with state["decode_lock"]:
            while state["decoded_sprites"]:
                _, image = state["decoded_sprites"].popitem()
                try:
                    image.close()
                except Exception:
                    pass
