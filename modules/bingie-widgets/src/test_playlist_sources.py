#!/usr/bin/python
# -*- coding: utf-8 -*-

import unittest

from resources.lib.playlist_sources import (
    discover_playlist_routes,
    merge_playlists,
)


class FakeWindow(object):
    def __init__(self, properties):
        self.properties = properties

    def getProperty(self, name):
        return self.properties.get(name, "")


class PlaylistSourcesTests(unittest.TestCase):
    def test_discovers_only_runtime_jellyfin_playlist_routes(self):
        window = FakeWindow({
            "Jellyfin.wnodes.total": "4",
            "Jellyfin.wnodes.0.type": "movies",
            "Jellyfin.wnodes.0.content": "plugin://plugin.video.jellyfin/?mode=browse",
            "Jellyfin.wnodes.1.type": "playlists",
            "Jellyfin.wnodes.1.content": "plugin://plugin.video.jellyfin/?mode=playlists&server=one",
            "Jellyfin.wnodes.2.type": "playlists",
            "Jellyfin.wnodes.2.path": "plugin://plugin.video.jellyfin/?mode=playlists&server=two",
            "Jellyfin.wnodes.3.type": "playlists",
            "Jellyfin.wnodes.3.content": "plugin://untrusted.example/?id=three",
        })

        self.assertEqual(discover_playlist_routes(window), [
            "plugin://plugin.video.jellyfin/?mode=playlists&server=one",
            "plugin://plugin.video.jellyfin/?mode=playlists&server=two",
        ])

    def test_invalid_count_is_an_empty_listing(self):
        self.assertEqual(
            discover_playlist_routes(FakeWindow({"Jellyfin.wnodes.total": "bad"})),
            [],
        )

    def test_merges_servers_and_deduplicates_by_runtime_folder(self):
        first = [
            {"label": "Family", "file": "plugin://plugin.video.jellyfin/?folder=a"},
            {"label": "Weekend", "file": "plugin://plugin.video.jellyfin/?folder=b"},
        ]
        second = [
            {"label": "Renamed Family", "file": "plugin://plugin.video.jellyfin/?folder=a"},
            {"label": "Awards", "file": "plugin://plugin.video.jellyfin/?folder=c"},
        ]

        self.assertEqual(merge_playlists([first, second]), [
            first[0], first[1], second[1],
        ])

    def test_limit_applies_after_deduplication(self):
        items = [
            {"label": str(index), "file": "path://%d" % index}
            for index in range(3)
        ]
        self.assertEqual(merge_playlists([items], limit=2), items[:2])


if __name__ == "__main__":
    unittest.main()
