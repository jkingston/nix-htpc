import unittest

from resources.lib.episode_progress import CONTINUE, UP_NEXT
from resources.lib.episode_progress_cache import EpisodeProgressCache


def episode(
        episode_id, number, show_id=1, played=0, lastplayed="", resume=0):
    return {
        "episodeid": episode_id,
        "tvshowid": show_id,
        "season": 1,
        "episode": number,
        "playcount": played,
        "lastplayed": lastplayed,
        "resume": {"position": resume, "total": 1800},
    }


class FakeCache(object):
    def __init__(self):
        self.items = {}

    def get(self, key, checksum=None):
        return self.items.get((key, checksum))

    def set(self, key, value, checksum=None):
        self.items[(key, checksum)] = value


class FakeKodiDb(object):
    def __init__(self, episodes):
        self.episode_items = episodes
        self.calls = []

    def episodes(self, **kwargs):
        self.calls.append(kwargs)
        return self.episode_items


class EpisodeProgressCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = FakeCache()
        self.kodidb = FakeKodiDb([
            episode(1, 1, show_id=10, lastplayed="2026-01-02", resume=30),
            episode(2, 2, show_id=10),
            episode(3, 1, show_id=20, played=1, lastplayed="2026-01-01"),
            episode(4, 2, show_id=20),
            episode(5, 1, show_id=30),
        ])
        self.options = {
            "_progress_checksum": "generation-1",
        }

    def test_snapshot_resolves_every_show_but_keeps_progress_separate(self):
        snapshot = EpisodeProgressCache(
            self.cache, self.kodidb, self.options
        ).get_or_build()

        progress = {
            decision.tvshowid: decision.state
            for decision in snapshot.progress
        }
        self.assertEqual(progress, {10: CONTINUE, 20: UP_NEXT})
        self.assertEqual(snapshot.primary(10).target_episodeid, 1)
        self.assertEqual(snapshot.primary(20).target_episodeid, 4)
        self.assertEqual(snapshot.primary(30).target_episodeid, 5)

    def test_second_consumer_reuses_snapshot_without_querying_kodi(self):
        EpisodeProgressCache(
            self.cache, self.kodidb, self.options
        ).get_or_build()
        second_db = FakeKodiDb([])

        snapshot = EpisodeProgressCache(
            self.cache, second_db, self.options
        ).get_or_build()

        self.assertEqual(second_db.calls, [])
        self.assertEqual(snapshot.primary(20).target_episodeid, 4)

    def test_new_episode_generation_rebuilds_snapshot(self):
        EpisodeProgressCache(
            self.cache, self.kodidb, self.options
        ).get_or_build()
        new_options = {"_progress_checksum": "generation-2"}

        EpisodeProgressCache(
            self.cache, self.kodidb, new_options
        ).get_or_build()

        self.assertEqual(len(self.kodidb.calls), 2)

    def test_cache_identity_includes_filters_and_specials(self):
        filtered_options = {
            "_progress_checksum": "generation-1",
            "tag": "Documentary",
            "path": "/tv/",
        }

        EpisodeProgressCache(
            self.cache, self.kodidb, filtered_options, include_specials=True
        ).get_or_build()

        self.assertEqual(self.kodidb.calls[-1]["filters"], [
            {"operator": "contains", "field": "tag", "value": "Documentary"},
            {"operator": "startswith", "field": "path", "value": "/tv/"},
        ])


if __name__ == "__main__":
    unittest.main()
