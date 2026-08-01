# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import os
import threading
from collections import OrderedDict


DEFAULT_FRAME_CACHE_BYTES = 24 * 1024 * 1024


class PlaybackFrameCache(object):
    """Thread-safe byte-bounded cache of immutable playback frame files."""

    def __init__(self, root, byte_limit=DEFAULT_FRAME_CACHE_BYTES):
        self.root = root
        self.byte_limit = max(0, int(byte_limit))
        self._items = OrderedDict()
        self._bytes = 0
        self._pinned = set()
        self._lock = threading.RLock()
        os.makedirs(root, exist_ok=True)

    @property
    def byte_size(self):
        with self._lock:
            return self._bytes

    def __len__(self):
        with self._lock:
            return len(self._items)

    def pin(self, keys):
        with self._lock:
            self._pinned.update(int(key) for key in keys)

    def get(self, key):
        key = int(key)
        with self._lock:
            entry = self._items.pop(key, None)
            if entry is None:
                return None
            path, size = entry
            if not os.path.exists(path):
                self._bytes -= size
                return None
            self._items[key] = entry
            return path

    def put(self, key, path):
        key = int(key)
        try:
            size = os.path.getsize(path)
        except OSError:
            return False

        evicted = []
        with self._lock:
            previous = self._items.pop(key, None)
            if previous is not None:
                self._bytes -= previous[1]
                if previous[0] != path:
                    evicted.append(previous[0])

            self._items[key] = (path, size)
            self._bytes += size
            while self._bytes > self.byte_limit:
                victim = next(
                    (
                        candidate
                        for candidate in self._items
                        if candidate not in self._pinned
                    ),
                    None,
                )
                if victim is None:
                    # Pinning is best effort: the byte ceiling is a hard
                    # process-memory budget even for chapter frames.
                    victim = next(iter(self._items), None)
                if victim is None:
                    break
                victim_path, victim_size = self._items.pop(victim)
                self._bytes -= victim_size
                if victim_path != path:
                    evicted.append(victim_path)

            retained = key in self._items

        for old_path in evicted:
            try:
                os.unlink(old_path)
            except OSError:
                pass
        if not retained:
            try:
                os.unlink(path)
            except OSError:
                pass
        return retained

    def items(self):
        with self._lock:
            return [
                (key, value[0])
                for key, value in self._items.items()
                if os.path.exists(value[0])
            ]


def sprite_for_frame(frame, info):
    per_sprite = max(1, int(info["TileWidth"])) * max(
        1, int(info["TileHeight"])
    )
    return max(0, int(frame)) // per_sprite


def sprite_order(info, current_frame=0):
    """Return every sprite, nearest to the current frame first."""
    count = max(1, int(info["ThumbnailCount"]))
    per_sprite = max(1, int(info["TileWidth"])) * max(
        1, int(info["TileHeight"])
    )
    sprite_count = (count + per_sprite - 1) // per_sprite
    current = min(sprite_count - 1, sprite_for_frame(current_frame, info))
    ordered = [current]
    distance = 1
    while len(ordered) < sprite_count:
        forward = current + distance
        backward = current - distance
        if forward < sprite_count:
            ordered.append(forward)
        if backward >= 0:
            ordered.append(backward)
        distance += 1
    return ordered


class PlaybackWorkQueue(object):
    """Latest foreground request plus a deduplicated whole-title warm queue."""

    def __init__(self, sprites):
        self._condition = threading.Condition(threading.RLock())
        self._request = None
        self._sprites = list(sprites)
        self._remaining = set(self._sprites)
        self._closed = False

    def submit_request(self, request, sprite):
        sprite = int(sprite)
        with self._condition:
            if self._closed:
                return False
            self._request = request
            if sprite in self._remaining:
                self._sprites.remove(sprite)
                self._sprites.insert(0, sprite)
            self._condition.notify_all()
            return True

    def take(self, abort):
        with self._condition:
            while (
                not self._closed
                and not abort.is_set()
                and self._request is None
                and not self._sprites
            ):
                self._condition.wait(0.05)
            if self._closed or abort.is_set():
                return None
            if self._request is not None:
                request = self._request
                self._request = None
                return "request", request
            sprite = self._sprites.pop(0)
            self._remaining.discard(sprite)
            return "warm", sprite

    def close(self):
        with self._condition:
            self._closed = True
            self._request = None
            self._sprites = []
            self._remaining.clear()
            self._condition.notify_all()

    @property
    def remaining(self):
        with self._condition:
            return len(self._sprites)

    @property
    def has_request(self):
        with self._condition:
            return self._request is not None
