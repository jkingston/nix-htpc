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

# Legacy properties consumed by the current BINGIE skin.
PREVIEW_PATH = "jellyfin.htpc.seekpreview"
PREVIEW_CHAPTER = "jellyfin.htpc.seekpreviewchapter"
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
CHAPTER_RETRY_BACKOFFS = (0.20, 0.60)


class PreviewFailure(Exception):
    def __init__(self, message, transient=True):
        super(PreviewFailure, self).__init__(message)
        self.transient = bool(transient)


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
    """Condition-backed queue with one replaceable pending request."""

    def __init__(self):
        self._condition = threading.Condition(threading.RLock())
        self._pending = None
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
            self._latest_key = request["key"]
            self._pending = request
            self._condition.notify_all()
            return True

    def clear(self):
        with self._condition:
            self._latest_key = None
            self._pending = None
            self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()

    def is_latest(self, key):
        with self._condition:
            return not self._closed and self._latest_key == key

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


def select_trickplay(trickplay, media_source_id, preferred_width=320):
    """Select the most useful TrickplayInfo from an item response."""
    if not isinstance(trickplay, dict) or not trickplay:
        return None, None

    source = trickplay.get(media_source_id)
    if source is None and all(str(key).isdigit() for key in trickplay):
        source = trickplay
    if source is None:
        source = next(
            (value for value in trickplay.values() if isinstance(value, dict)),
            None,
        )
    if not isinstance(source, dict):
        return None, None

    widths = []
    for key, value in source.items():
        try:
            width = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            widths.append((width, value))
    if not widths:
        return None, None

    width, info = min(widths, key=lambda value: abs(value[0] - preferred_width))
    required = (
        "Interval",
        "ThumbnailCount",
        "TileWidth",
        "TileHeight",
        "Width",
        "Height",
    )
    if not all(info.get(key) for key in required):
        return None, None
    return width, info


def chapter_for_time(chapters, seconds):
    """Compatibility helper for raw Jellyfin chapter metadata."""
    selected = None
    for index, chapter in enumerate(chapters or []):
        try:
            start = float(chapter.get("StartPositionTicks", 0)) / 10000000.0
        except (TypeError, ValueError):
            continue
        if start > seconds:
            break
        selected = (index, chapter)
    return selected


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


def chapter_entry_for_time(chapters, seconds):
    selected = None
    for chapter in chapters or []:
        if float(chapter["time_seconds"]) > float(seconds):
            break
        selected = chapter
    return selected


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
    key = (
        payload["playback"],
        payload["revision"],
        payload["seek_generation"],
        target_text,
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
            metadata = client.jellyfin.get_item(item["Id"]) or {}
            width, info = select_trickplay(
                metadata.get("Trickplay"),
                item.get("MediaSourceId"),
            )
            duration = media_duration_seconds(item, metadata, self.player)
            chapters = sanitize_chapters(metadata.get("Chapters") or [], duration)
            state = self._new_state(
                item,
                client,
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
                last_observed = observed
                last_target = numeric_target
        except Exception as error:
            if not abort.is_set():
                LOG.warning("HTPC preview bridge unavailable: %s", error)
        finally:
            abort.set()
            if state is not None:
                self._close_state(state)
                self._clear_if_owned(state)
            shutil.rmtree(cache_root, ignore_errors=True)

    def _new_state(
        self,
        item,
        client,
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
            "media_source_id": item.get("MediaSourceId"),
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
                    LOG.warning("Exact trickplay request failed: %s", error)

    def _process_preview_request(self, state, request, abort):
        attempts = len(PREVIEW_RETRY_BACKOFFS) + 1
        for attempt in range(attempts):
            if not self._request_is_current(state, request, abort):
                return False
            try:
                source_path = self._resolve_frame_path(
                    state,
                    request["frame"],
                    abort,
                    foreground=True,
                )
            except PreviewFailure as error:
                retry = error.transient and attempt < len(PREVIEW_RETRY_BACKOFFS)
                if not retry:
                    self._clear_preview_if_current(state, request, abort)
                    return False
                if not state["request_slot"].wait_retry(
                    request["key"],
                    PREVIEW_RETRY_BACKOFFS[attempt],
                    abort,
                ):
                    return False
                continue

            if not self._request_is_current(state, request, abort):
                return False
            if not self._publish_preview(state, request, source_path, abort):
                return False
            self._queue_one_neighbor(state, request)
            return True
        return False

    def _request_is_current(self, state, request, abort):
        return (
            not abort.is_set()
            and request["token"]["playback"] == state["playback_token"]
            and request["token"]["revision"] == state["revision"]
            and state["request_slot"].is_latest(request["key"])
            and _property_true(window(SEEK_ACTIVE))
            and window(SEEK_GENERATION) == request["token"]["seek_generation"]
            and window(SEEK_TARGET) == request["target_text"]
        )

    def _publish_preview(self, state, request, source_path, abort):
        staged_path = state["output_slots"].stage(source_path)
        if not self._request_is_current(state, request, abort):
            return False

        chapter = chapter_entry_for_time(
            state["chapters"],
            request["target_seconds"],
        )
        chapter_label = chapter["label"] if chapter is not None else ""
        payload = request["token"]
        token_json = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._property_lock:
            if not self._request_is_current(state, request, abort):
                return False
            # The new immutable slot is installed first while the old target
            # keeps legacy skins from exposing it. The complete token and exact
            # target are the final commit markers.
            window(PREVIEW_PATH, staged_path)
            state["output_slots"].activate(staged_path)
            window(PREVIEW_PLAYBACK, payload["playback"])
            window(PREVIEW_GENERATION, payload["seek_generation"])
            window(PREVIEW_SAMPLE, str(payload["sample_seconds"]))
            window(PREVIEW_FRAME, str(payload["frame_index"]))
            window(PREVIEW_REVISION, str(payload["revision"]))
            if chapter_label:
                window(PREVIEW_CHAPTER, chapter_label)
            else:
                window(PREVIEW_CHAPTER, clear=True)
            window(PREVIEW_TOKEN, token_json)
            window(PREVIEW_TARGET, request["target_text"])
        return True

    def _clear_preview_if_current(self, state, request, abort):
        if self._request_is_current(state, request, abort):
            self._clear_preview_properties(state)

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
                        "Directional trickplay neighbor %s unavailable: %s",
                        request["frame"],
                        error,
                    )

    def _resolve_frame_path(self, state, frame, abort, foreground):
        cached = state["frame_cache"].get(int(frame))
        if cached is not None:
            return cached
        if state["info"] is None:
            raise PreviewFailure("exact trickplay metadata is unavailable", False)
        if abort.is_set():
            raise PreviewFailure("preview aborted", False)

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
            try:
                if image is None:
                    from PIL import Image

                    image = Image.open(io.BytesIO(sprite_data))
                    image.load()
                state["decoded_sprites"][sprite] = image
                while len(state["decoded_sprites"]) > DECODED_SPRITE_LIMIT:
                    _, old_image = state["decoded_sprites"].popitem(last=False)
                    old_image.close()

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
            except ImportError as error:
                raise PreviewFailure("Pillow is unavailable: %s" % error, False)
            except Exception as error:
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
                raise PreviewFailure("frame %s decode: %s" % (frame, error), True)
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
                raise PreviewFailure("preview aborted", False)
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
                    "sprite %s: %s" % (sprite, error),
                    self._is_transient_download_error(error),
                )
            if not isinstance(sprite_data, (bytes, bytearray)) or not sprite_data:
                raise PreviewFailure("sprite %s was empty" % sprite, True)
            if abort.is_set():
                raise PreviewFailure("preview aborted", False)
            state["sprite_cache"].put(sprite, sprite_data)
            return sprite_data
        finally:
            with condition:
                state["inflight_sprites"].discard(sprite)
                condition.notify_all()

    @staticmethod
    def _is_transient_download_error(error):
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        if status is None:
            return True
        try:
            status = int(status)
        except (TypeError, ValueError):
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
                        "Chapter image %s unavailable, trying trickplay: %s",
                        entry["index"],
                        error,
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
            LOG.debug(
                "Trickplay fallback for chapter %s unavailable: %s",
                entry["index"],
                error,
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
            # Clear the legacy commit marker first so a stale image is hidden.
            window(PREVIEW_TARGET, clear=True)
            for key in (
                PREVIEW_TOKEN,
                PREVIEW_PATH,
                PREVIEW_CHAPTER,
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
