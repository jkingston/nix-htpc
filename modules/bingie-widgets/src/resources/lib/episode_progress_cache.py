#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Generation-scoped cache for lightweight per-series playback decisions."""

from resources.lib.episode_progress import (
    SeriesProgress,
    resolve_series_primaries,
    resolve_series_progress,
)


CACHE_SCHEMA = "series-progress-snapshot-v1"
EPISODE_STATE_FIELDS = [
    "tvshowid", "season", "episode", "playcount", "lastplayed", "resume",
]


class EpisodeProgressSnapshot(object):
    """Playback decisions derived from one minimal Kodi episode query."""

    def __init__(self, progress, primaries):
        self.progress = progress
        self._primaries = {
            str(decision.tvshowid): decision for decision in primaries
        }

    def primary(self, tvshow_id):
        return self._primaries.get(str(tvshow_id))

    def serialize(self):
        return {
            "progress": [_serialize(decision) for decision in self.progress],
            "primaries": [
                _serialize(decision) for decision in self._primaries.values()
            ],
        }

    @classmethod
    def deserialize(cls, payload):
        return cls(
            [_deserialize(item) for item in payload.get("progress", [])],
            [_deserialize(item) for item in payload.get("primaries", [])],
        )


class EpisodeProgressCache(object):
    """Share a progress snapshot between independent widget invocations."""

    def __init__(self, cache, kodidb, options, include_specials=False):
        self.cache = cache
        self.kodidb = kodidb
        self.options = options
        self.include_specials = include_specials
        self.last_cache_hit = False

    def cached(self):
        payload = self.cache.get(
            self._cache_key(), checksum=self.options.get("_progress_checksum")
        )
        if payload is None:
            self.last_cache_hit = False
            return None
        self.last_cache_hit = True
        return EpisodeProgressSnapshot.deserialize(payload)

    def get_or_build(self):
        snapshot = self.cached()
        if snapshot is not None:
            return snapshot

        episodes = self.kodidb.episodes(
            filters=self._filters(),
            fields=EPISODE_STATE_FIELDS,
        )
        progress = resolve_series_progress(episodes, self.include_specials)
        snapshot = EpisodeProgressSnapshot(
            progress,
            resolve_series_primaries(
                episodes, self.include_specials, progress_decisions=progress
            ),
        )
        self.cache.set(
            self._cache_key(),
            snapshot.serialize(),
            checksum=self.options.get("_progress_checksum"),
        )
        return snapshot

    def _filters(self):
        filters = []
        if self.options.get("tag"):
            filters.append({
                "operator": "contains",
                "field": "tag",
                "value": self.options["tag"],
            })
        if self.options.get("path"):
            filters.append({
                "operator": "startswith",
                "field": "path",
                "value": self.options["path"],
            })
        return filters

    def _cache_key(self):
        return "Bingie.Widgets.%s.specials=%s.tag=%s.path=%s" % (
            CACHE_SCHEMA,
            self.include_specials,
            self.options.get("tag") or "",
            self.options.get("path") or "",
        )


def _serialize(decision):
    return dict(decision._asdict())


def _deserialize(payload):
    return SeriesProgress(**payload)
