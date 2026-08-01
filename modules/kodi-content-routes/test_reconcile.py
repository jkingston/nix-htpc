import os
from pathlib import Path
import tempfile
import unittest

from reconcile import RouteError, discover_routes, reconcile


def write_node(directory: Path, tag: str):
    directory.mkdir()
    (directory / "all.xml").write_text(
        '<node type="filter"><rule field="tag" operator="is">'
        "<value>%s</value></rule></node>" % tag
    )
    (directory / "nextepisodes.xml").write_text("<node />")


class ContentRouteTest(unittest.TestCase):
    def test_discovers_roles_without_exposing_generated_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            anime = root / "jellyfintvshows-private-anime-id"
            shows = root / "jellyfintvshows-private-shows-id"
            write_node(anime, "Anime")
            write_node(shows, "Shows")

            routes = discover_routes(root)

            self.assertEqual(routes, {"anime": anime, "tvshows": shows})

    def test_publishes_relative_atomic_aliases_idempotently(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            anime = root / "jellyfintvshows-anime"
            shows = root / "jellyfintvshows-shows"
            write_node(anime, "Anime")
            write_node(shows, "Shows")

            self.assertEqual(
                reconcile(root),
                {"anime": "htpc-anime", "tvshows": "htpc-tvshows"},
            )
            self.assertEqual(os.readlink(root / "htpc-anime"), anime.name)
            self.assertEqual(os.readlink(root / "htpc-tvshows"), shows.name)
            self.assertEqual(
                reconcile(root),
                {"anime": "htpc-anime", "tvshows": "htpc-tvshows"},
            )

    def test_missing_role_is_not_fabricated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_node(root / "jellyfintvshows-anime", "Anime")

            self.assertEqual(reconcile(root), {"anime": "htpc-anime"})
            self.assertFalse((root / "htpc-tvshows").exists())

    def test_ambiguous_role_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_node(root / "jellyfintvshows-one", "Anime")
            write_node(root / "jellyfintvshows-two", "Anime")

            with self.assertRaises(RouteError):
                reconcile(root)

    def test_refuses_to_replace_user_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_node(root / "jellyfintvshows-anime", "Anime")
            (root / "htpc-anime").mkdir()

            with self.assertRaises(RouteError):
                reconcile(root)


if __name__ == "__main__":
    unittest.main()
