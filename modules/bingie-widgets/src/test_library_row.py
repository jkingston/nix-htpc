import unittest

from resources.lib.library_row import build_library_row


class FakeKodiDb(object):
    def __init__(self, items):
        self.items = items
        self.calls = []

    def movies(self, **kwargs):
        self.calls.append(("movies", kwargs))
        return list(self.items[:kwargs["limits"][1]])

    def tvshows(self, **kwargs):
        self.calls.append(("tvshows", kwargs))
        return list(self.items[:kwargs["limits"][1]])


class LibraryRowTest(unittest.TestCase):
    def test_full_library_link_replaces_the_last_preview_slot(self):
        database = FakeKodiDb([
            {"movieid": index, "label": "Movie %02d" % index, "file": "movie-%d" % index}
            for index in range(20)
        ])

        items = build_library_row(database, "movies", 15)

        self.assertEqual(len(items), 15)
        self.assertEqual([item["movieid"] for item in items[:-1]], list(range(14)))
        self.assertEqual(items[-1]["label"], "View All")
        self.assertEqual(items[-1]["file"], "library://video/movies/titles.xml")
        self.assertEqual(items[-1]["extraproperties"]["BingieViewAll"], "true")
        self.assertEqual(database.calls[0][1]["limits"], (0, 15))

    def test_small_library_has_no_redundant_view_all_tile(self):
        database = FakeKodiDb([
            {"movieid": index, "label": "Movie %02d" % index, "file": "movie-%d" % index}
            for index in range(4)
        ])

        items = build_library_row(database, "movies", 15)

        self.assertEqual(len(items), 4)
        self.assertNotIn("View All", [item["label"] for item in items])

    def test_tv_show_processor_is_applied_to_preview_items_only(self):
        database = FakeKodiDb([
            {"tvshowid": index, "label": "Show %02d" % index}
            for index in range(20)
        ])

        items = build_library_row(
            database,
            "tvshows",
            3,
            lambda item: dict(item, processed=True),
        )

        self.assertTrue(items[0]["processed"])
        self.assertTrue(items[1]["processed"])
        self.assertNotIn("processed", items[2])
        self.assertEqual(items[2]["file"], "library://video/tvshows/titles.xml")


if __name__ == "__main__":
    unittest.main()
