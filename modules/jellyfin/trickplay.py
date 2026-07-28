# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

import io
import os
import shutil
import threading
import time
from collections import OrderedDict, deque

import requests
import xbmc

from .helper import LazyLogger, window
from .helper.utils import translate_path


LOG = LazyLogger(__name__)

PREVIEW_PATH = "jellyfin.htpc.seekpreview"
PREVIEW_TIME = "jellyfin.htpc.seekpreviewtime"
PREVIEW_CHAPTER = "jellyfin.htpc.seekpreviewchapter"

SEEK_POLL_SECONDS = 0.05
PREFETCH_WAIT_SECONDS = 1.5
SPRITE_CACHE_BYTES = 16 * 1024 * 1024
DECODED_SPRITE_LIMIT = 1
FRAME_CACHE_LIMIT = 48
NEIGHBOR_FRAME_RADIUS = 3


def parse_time_label(value):
    """Convert Kodi's Player.SeekTime label to seconds."""
    if not value:
        return None

    try:
        parts = [int(part) for part in value.strip().split(":")]
    except (TypeError, ValueError):
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


def tile_for_frame(frame, info):
    """Return the sprite and crop box for a Jellyfin trickplay frame."""
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
    """Return frame, sprite and crop box for a Jellyfin TrickplayInfo."""
    return tile_for_frame(frame_for_time(seconds, info), info)


def adjacent_sprites(frame, info, direction=0):
    """Return valid adjacent sprites, preferring the seek direction."""
    count = max(1, int(info["ThumbnailCount"]))
    per_sprite = max(1, int(info["TileWidth"])) * max(
        1, int(info["TileHeight"])
    )
    sprite_count = (count + per_sprite - 1) // per_sprite
    current = min(max(0, int(frame)), count - 1) // per_sprite
    offsets = (-1, 1) if direction < 0 else (1, -1)

    return tuple(
        current + offset
        for offset in offsets
        if 0 <= current + offset < sprite_count
    )


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

    width, info = min(widths, key=lambda candidate: abs(candidate[0] - preferred_width))
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
    selected = None
    for index, chapter in enumerate(chapters or []):
        start = float(chapter.get("StartPositionTicks", 0)) / 10000000.0
        if start > seconds:
            break
        selected = (index, chapter)

    return selected


class TrickplayPreviewManager(object):
    """Provide local, token-free seek preview images to Kodi skins."""

    def __init__(self, player):
        self.player = player
        self.abort = None
        self.thread = None
        self.cache_root = None

    def start(self, item):
        self.stop()

        if item.get("Type") not in ("Movie", "Episode", "Video"):
            return

        abort = threading.Event()
        cache_root = os.path.join(
            translate_path("special://temp"),
            "jellyfin-trickplay",
            item["Id"],
        )
        self.abort = abort
        self.cache_root = cache_root
        self.thread = threading.Thread(
            target=self._run,
            args=(item, abort, cache_root),
            name="jellyfin-trickplay",
        )
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        abort = self.abort
        thread = self.thread
        cache_root = self.cache_root

        self.abort = None
        self.thread = None
        self.cache_root = None

        if abort is not None:
            abort.set()
        if thread is not None and thread.is_alive():
            thread.join(1.0)

        self._clear_properties()
        if cache_root and (thread is None or not thread.is_alive()):
            shutil.rmtree(cache_root, ignore_errors=True)

    def _run(self, item, abort, cache_root):
        state = None
        try:
            os.makedirs(cache_root, exist_ok=True)
            client = item["Server"]
            metadata = client.jellyfin.get_item(item["Id"]) or {}
            width, info = select_trickplay(
                metadata.get("Trickplay"),
                item.get("MediaSourceId"),
            )
            chapters = metadata.get("Chapters") or []
            state = {
                "item_id": item["Id"],
                "media_source_id": item.get("MediaSourceId"),
                "client": client,
                "width": width,
                "info": info,
                "chapters": chapters,
                "cache_root": cache_root,
                "sprites": OrderedDict(),
                "sprite_bytes": 0,
                "decoded_sprites": OrderedDict(),
                "frames": OrderedDict(),
                "failed_sprites": set(),
                "downloading_sprites": set(),
                "prefetch_queue": deque(),
                "cache_condition": threading.Condition(threading.RLock()),
                "session": self._new_session(client),
                "prefetch_session": self._new_session(client),
                "prefetch_thread": None,
                "pillow_available": True,
                "last_seconds": None,
            }

            if info is not None:
                prefetch_thread = threading.Thread(
                    target=self._prefetch_worker,
                    args=(state, abort),
                    name="jellyfin-trickplay-prefetch",
                )
                prefetch_thread.daemon = True
                state["prefetch_thread"] = prefetch_thread
                prefetch_thread.start()

            try:
                current = self.player.getTime()
            except Exception:
                current = 0
            self._ensure_preview(state, current, abort)

            last_target = None
            while not abort.wait(SEEK_POLL_SECONDS):
                if not xbmc.getCondVisibility("Player.Seeking"):
                    last_target = None
                    continue

                target = parse_time_label(
                    xbmc.getInfoLabel("Player.SeekTime(hh:mm:ss)")
                )
                if target is None or target == last_target:
                    continue

                last_target = target
                self._ensure_preview(state, target, abort)
        except Exception as error:
            if not abort.is_set():
                LOG.warning("HTPC trickplay preview unavailable: %s", error)
        finally:
            abort.set()
            if state is not None:
                self._close_state(state, abort)

    def _ensure_preview(self, state, seconds, abort):
        chapter = chapter_for_time(state["chapters"], seconds)
        chapter_name = ""
        if chapter is not None:
            chapter_name = chapter[1].get("Name") or ""

        # Time and chapter text must not wait behind a cold sprite request.
        window(PREVIEW_TIME, format_time(seconds))
        if chapter_name:
            window(PREVIEW_CHAPTER, chapter_name)
        else:
            window(PREVIEW_CHAPTER, clear=True)

        previous = state["last_seconds"]
        direction = 0
        if previous is not None:
            direction = 1 if seconds > previous else -1 if seconds < previous else 0
        state["last_seconds"] = seconds

        path = None
        frame = None
        if state["info"] is not None:
            frame = frame_for_time(seconds, state["info"])
            path = self._trickplay_frame_by_index(state, frame, abort)
        if path is None and chapter is not None:
            path = self._chapter_frame(state, chapter[0], abort)

        if abort.is_set():
            return

        if path:
            window(PREVIEW_PATH, path)
        else:
            window(PREVIEW_PATH, clear=True)

        if frame is not None and path:
            self._warm_neighbor_frames(state, frame, direction, abort)
            self._queue_adjacent_sprites(state, frame, direction)

    def _trickplay_frame_by_index(self, state, frame, abort):
        frame, sprite, box = tile_for_frame(frame, state["info"])
        if frame in state["frames"]:
            path = state["frames"].pop(frame)
            state["frames"][frame] = path
            return path
        if sprite in state["failed_sprites"]:
            return None

        sprite_data = self._sprite_data(state, sprite, abort)
        if sprite_data is None or abort.is_set():
            return None

        image = self._decoded_sprite(state, sprite, sprite_data)
        if image is None:
            return None

        path = os.path.join(state["cache_root"], "frame-%06d.jpg" % frame)
        temporary = path + ".tmp"
        image.crop(box).convert("RGB").save(temporary, "JPEG", quality=88)
        os.replace(temporary, path)

        self._remember_frame(state, frame, path)

        return path

    def _warm_neighbor_frames(self, state, frame, direction, abort):
        offsets = []
        for distance in range(1, NEIGHBOR_FRAME_RADIUS + 1):
            if direction < 0:
                offsets.extend((-distance, distance))
            else:
                offsets.extend((distance, -distance))

        _, target_sprite, _ = tile_for_frame(frame, state["info"])
        for offset in offsets:
            neighbor = frame + offset
            if not 0 <= neighbor < int(state["info"]["ThumbnailCount"]):
                continue
            _, neighbor_sprite, _ = tile_for_frame(neighbor, state["info"])
            if neighbor_sprite != target_sprite:
                continue
            if abort.is_set():
                return
            self._trickplay_frame_by_index(state, neighbor, abort)

    def _sprite_data(self, state, sprite, abort):
        if abort.is_set():
            return None

        condition = state["cache_condition"]
        with condition:
            cached = state["sprites"].pop(sprite, None)
            if cached is not None:
                state["sprites"][sprite] = cached
                return cached
            if sprite in state["failed_sprites"]:
                return None

            deadline = time.monotonic() + PREFETCH_WAIT_SECONDS
            while sprite in state["downloading_sprites"] and not abort.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                condition.wait(min(SEEK_POLL_SECONDS, remaining))
                cached = state["sprites"].pop(sprite, None)
                if cached is not None:
                    state["sprites"][sprite] = cached
                    return cached

            if abort.is_set():
                return None
            state["downloading_sprites"].add(sprite)

        handler = "Videos/%s/Trickplay/%s/%s.jpg" % (
            state["item_id"],
            state["width"],
            sprite,
        )
        data = None
        error = None
        try:
            data = self._download(
                state["client"],
                handler,
                {"MediaSourceId": state["media_source_id"]},
                session=state["session"],
            )
        except Exception as download_error:
            error = download_error

        with condition:
            state["downloading_sprites"].discard(sprite)
            if data is not None and not abort.is_set():
                self._remember_sprite_locked(state, sprite, data)
            elif error is not None and sprite not in state["sprites"]:
                state["failed_sprites"].add(sprite)
            condition.notify_all()

        if error is not None:
            LOG.debug("Trickplay sprite %s unavailable: %s", sprite, error)
            return None

        return data if not abort.is_set() else None

    def _decoded_sprite(self, state, sprite, sprite_data):
        image = state["decoded_sprites"].pop(sprite, None)
        if image is not None:
            state["decoded_sprites"][sprite] = image
            return image
        if not state["pillow_available"]:
            return None

        try:
            from PIL import Image
        except ImportError:
            state["pillow_available"] = False
            LOG.warning("Pillow is unavailable; using chapter-image previews")
            return None

        image = Image.open(io.BytesIO(sprite_data))
        image.load()
        state["decoded_sprites"][sprite] = image
        while len(state["decoded_sprites"]) > DECODED_SPRITE_LIMIT:
            _, old_image = state["decoded_sprites"].popitem(last=False)
            old_image.close()

        return image

    @staticmethod
    def _remember_sprite_locked(state, sprite, data):
        previous = state["sprites"].pop(sprite, None)
        if previous is not None:
            state["sprite_bytes"] -= len(previous)
        state["failed_sprites"].discard(sprite)
        state["sprites"][sprite] = data
        state["sprite_bytes"] += len(data)

        while state["sprite_bytes"] > SPRITE_CACHE_BYTES and len(
            state["sprites"]
        ) > 1:
            _, evicted = state["sprites"].popitem(last=False)
            state["sprite_bytes"] -= len(evicted)

    @staticmethod
    def _remember_frame(state, key, path):
        previous = state["frames"].pop(key, None)
        if previous is not None and previous != path:
            try:
                os.unlink(previous)
            except OSError:
                pass
        state["frames"][key] = path

        while len(state["frames"]) > FRAME_CACHE_LIMIT:
            _, old_path = state["frames"].popitem(last=False)
            try:
                os.unlink(old_path)
            except OSError:
                pass

    @staticmethod
    def _queue_adjacent_sprites(state, frame, direction):
        candidates = adjacent_sprites(frame, state["info"], direction)
        condition = state["cache_condition"]
        with condition:
            state["prefetch_queue"].clear()
            for sprite in candidates:
                if (
                    sprite not in state["sprites"]
                    and sprite not in state["failed_sprites"]
                    and sprite not in state["downloading_sprites"]
                ):
                    state["prefetch_queue"].append(sprite)
            condition.notify_all()

    def _prefetch_worker(self, state, abort):
        condition = state["cache_condition"]
        while not abort.is_set():
            with condition:
                while not state["prefetch_queue"] and not abort.is_set():
                    condition.wait(0.2)
                if abort.is_set():
                    return

                sprite = state["prefetch_queue"].popleft()
                if (
                    sprite in state["sprites"]
                    or sprite in state["failed_sprites"]
                    or sprite in state["downloading_sprites"]
                ):
                    continue
                state["downloading_sprites"].add(sprite)

            handler = "Videos/%s/Trickplay/%s/%s.jpg" % (
                state["item_id"],
                state["width"],
                sprite,
            )
            data = None
            try:
                data = self._download(
                    state["client"],
                    handler,
                    {"MediaSourceId": state["media_source_id"]},
                    session=state["prefetch_session"],
                )
            except Exception as error:
                if not abort.is_set():
                    LOG.debug(
                        "Trickplay sprite %s prefetch unavailable: %s",
                        sprite,
                        error,
                    )

            with condition:
                state["downloading_sprites"].discard(sprite)
                if data is not None and not abort.is_set():
                    self._remember_sprite_locked(state, sprite, data)
                condition.notify_all()

    @staticmethod
    def _close_state(state, abort):
        abort.set()
        condition = state["cache_condition"]
        with condition:
            condition.notify_all()

        prefetch_thread = state["prefetch_thread"]
        if prefetch_thread is not None and prefetch_thread.is_alive():
            prefetch_thread.join(1.0)

        state["session"].close()
        state["prefetch_session"].close()
        while state["decoded_sprites"]:
            _, image = state["decoded_sprites"].popitem()
            image.close()

    def _chapter_frame(self, state, index, abort):
        key = "chapter-%04d" % index
        if key in state["frames"]:
            path = state["frames"].pop(key)
            state["frames"][key] = path
            return path

        handler = "Items/%s/Images/Chapter/%s" % (state["item_id"], index)
        try:
            data = self._download(
                state["client"],
                handler,
                {"MaxWidth": 320, "format": "jpg"},
                session=state["session"],
            )
        except Exception as error:
            LOG.debug("Chapter image %s unavailable: %s", index, error)
            return None
        if abort.is_set():
            return None

        path = os.path.join(state["cache_root"], "%s.jpg" % key)
        temporary = path + ".tmp"
        with open(temporary, "wb") as output:
            output.write(data)
        os.replace(temporary, path)
        self._remember_frame(state, key, path)

        return path

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

    @staticmethod
    def _clear_properties():
        for key in (PREVIEW_PATH, PREVIEW_TIME, PREVIEW_CHAPTER):
            window(key, clear=True)
