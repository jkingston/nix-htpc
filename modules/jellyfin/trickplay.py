# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

import io
import os
import shutil
import threading

import requests
import xbmc

from .helper import LazyLogger, window
from .helper.utils import translate_path


LOG = LazyLogger(__name__)

PREVIEW_PATH = "jellyfin.htpc.seekpreview"
PREVIEW_TIME = "jellyfin.htpc.seekpreviewtime"
PREVIEW_CHAPTER = "jellyfin.htpc.seekpreviewchapter"


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


def tile_for_time(seconds, info):
    """Return frame, sprite and crop box for a Jellyfin TrickplayInfo."""
    interval = max(1, int(info["Interval"]))
    count = max(1, int(info["ThumbnailCount"]))
    columns = max(1, int(info["TileWidth"]))
    rows = max(1, int(info["TileHeight"]))
    width = max(1, int(info["Width"]))
    height = max(1, int(info["Height"]))

    frame = min(max(0, int(float(seconds) * 1000) // interval), count - 1)
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
                "sprites": {},
                "frames": {},
                "failed_sprites": set(),
            }

            try:
                current = self.player.getTime()
            except Exception:
                current = 0
            self._ensure_preview(state, current, abort)

            last_target = None
            while not abort.wait(0.1):
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

    def _ensure_preview(self, state, seconds, abort):
        chapter = chapter_for_time(state["chapters"], seconds)
        chapter_name = ""
        if chapter is not None:
            chapter_name = chapter[1].get("Name") or ""

        path = None
        if state["info"] is not None:
            path = self._trickplay_frame(state, seconds, abort)
        if path is None and chapter is not None:
            path = self._chapter_frame(state, chapter[0], abort)

        if abort.is_set():
            return

        if path:
            window(PREVIEW_PATH, path)
        else:
            window(PREVIEW_PATH, clear=True)
        window(PREVIEW_TIME, format_time(seconds))
        if chapter_name:
            window(PREVIEW_CHAPTER, chapter_name)
        else:
            window(PREVIEW_CHAPTER, clear=True)

    def _trickplay_frame(self, state, seconds, abort):
        frame, sprite, box = tile_for_time(seconds, state["info"])
        if frame in state["frames"]:
            return state["frames"][frame]
        if sprite in state["failed_sprites"]:
            return None

        sprite_data = state["sprites"].get(sprite)
        if sprite_data is None:
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
                )
            except Exception as error:
                state["failed_sprites"].add(sprite)
                LOG.debug("Trickplay sprite %s unavailable: %s", sprite, error)
                return None
            if abort.is_set():
                return None
            state["sprites"][sprite] = sprite_data
            if len(state["sprites"]) > 3:
                oldest = next(iter(state["sprites"]))
                if oldest != sprite:
                    state["sprites"].pop(oldest)

        path = os.path.join(state["cache_root"], "frame-%06d.jpg" % frame)
        temporary = path + ".tmp"
        try:
            from PIL import Image
        except ImportError:
            state["failed_sprites"].add(sprite)
            LOG.warning("Pillow is unavailable; using chapter-image previews")
            return None
        with Image.open(io.BytesIO(sprite_data)) as image:
            image.crop(box).convert("RGB").save(temporary, "JPEG", quality=88)
        os.replace(temporary, path)

        state["frames"][frame] = path
        if len(state["frames"]) > 48:
            oldest = next(iter(state["frames"]))
            old_path = state["frames"].pop(oldest)
            try:
                os.unlink(old_path)
            except OSError:
                pass

        return path

    def _chapter_frame(self, state, index, abort):
        key = "chapter-%04d" % index
        if key in state["frames"]:
            return state["frames"][key]

        handler = "Items/%s/Images/Chapter/%s" % (state["item_id"], index)
        try:
            data = self._download(
                state["client"],
                handler,
                {"MaxWidth": 320, "format": "jpg"},
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
        state["frames"][key] = path

        return path

    @staticmethod
    def _download(client, handler, params):
        server = client.config.data["auth.server"].rstrip("/")
        token = client.config.data["auth.token"]
        response = requests.get(
            "%s/%s" % (server, handler),
            params={key: value for key, value in params.items() if value is not None},
            headers={
                "Accept": "image/jpeg",
                "X-Emby-Token": token,
            },
            timeout=(2, 5),
            verify=client.config.data.get("auth.ssl", False),
        )
        response.raise_for_status()

        return response.content

    @staticmethod
    def _clear_properties():
        for key in (PREVIEW_PATH, PREVIEW_TIME, PREVIEW_CHAPTER):
            window(key, clear=True)
