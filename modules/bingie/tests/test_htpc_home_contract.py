from __future__ import annotations

import json
import os
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


BINGIE_ROOT = Path(__file__).resolve().parents[1]
SKIN_ROOT = Path(
    os.environ.get("BINGIE_SKIN_ROOT", str(BINGIE_ROOT / "src"))
).resolve()
SHORTCUTS_ROOT = SKIN_ROOT / "shortcuts"
CONTRACT_PATH = Path(__file__).parent / "fixtures" / "home_contract.json"


def shortcuts(name):
    return ET.parse(SHORTCUTS_ROOT / name).getroot().findall("shortcut")


def text(node, name):
    child = node.find(name)
    return "" if child is None else (child.text or "").strip()


def route(action):
    match = re.match(r"ActivateWindow\(Videos,(.*),return\)$", action)
    return match.group(1) if match else action


class PortableHomeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_contract_is_small_canonical_and_versioned(self):
        self.assertEqual(self.contract["schema_version"], 2)
        canonical = json.dumps(self.contract, indent=2) + "\n"
        self.assertEqual(CONTRACT_PATH.read_text(encoding="utf-8"), canonical)
        self.assertLess(CONTRACT_PATH.stat().st_size, 8 * 1024)

    def test_sidebar_has_only_portable_primary_destinations(self):
        actual = [
            [text(item, "label"), text(item, "action")]
            for item in shortcuts("mainmenu.DATA.xml")
        ]
        self.assertEqual(actual, self.contract["sidebar"])
        self.assertNotIn("Anime", [item[0] for item in actual])

    def test_sidebar_peer_destinations_replace_instead_of_stacking(self):
        actions = {
            text(item, "label"): text(item, "action")
            for item in shortcuts("mainmenu.DATA.xml")
        }
        for label in ("$LOCALIZE[137]", "$LOCALIZE[10000]", "TV Shows", "$LOCALIZE[342]"):
            self.assertRegex(actions[label], r"^ReplaceWindow\([^,]+\)$")
        self.assertEqual(actions["$LOCALIZE[10004]"], "ActivateWindow(Settings)")
        self.assertEqual(actions["Power"], "ActivateWindow(shutdownmenu)")

    def test_home_rows_and_presentation_are_declared_together(self):
        items = shortcuts("10000-1.DATA.xml")
        properties = json.loads(
            (SHORTCUTS_ROOT / "htpc.properties.json").read_text(encoding="utf-8")
        )
        by_slug = {}
        for group, slug, key, value in properties:
            self.assertEqual(group, "10000.1")
            by_slug.setdefault(slug, {})[key] = value

        actual = []
        for item in items:
            label = text(item, "label")
            slug = re.sub(r"[^a-z0-9]", "", label.casefold())
            actual.append([
                label,
                route(text(item, "action")),
                by_slug[slug]["widgetstyle"],
            ])
            self.assertEqual(by_slug[slug]["widgetLimit"], "15")
            self.assertEqual(by_slug[slug]["widgetTarget"], "videos")
            self.assertEqual(by_slug[slug]["widgetTags"], "disable")
        self.assertEqual(actual, self.contract["home_rows"])

    def test_library_hubs_expose_all_collections_and_genres(self):
        movie_labels = [text(item, "label") for item in shortcuts("moviehub.DATA.xml")]
        tv_labels = [text(item, "label") for item in shortcuts("tvshowhub.DATA.xml")]
        self.assertEqual(movie_labels, self.contract["movie_hub"])
        self.assertEqual(tv_labels, self.contract["tv_hub"])

        home_xml = (SKIN_ROOT / "1080i" / "IncludesHomeBingie.xml").read_text(
            encoding="utf-8"
        )
        self.assertIn("HTPC_Movie_Genres_Row", home_xml)
        self.assertIn("HTPC_TV_Genres_Row", home_xml)

    def test_recent_tv_routes_return_shows_not_episodes(self):
        for shortcut_file in ("10000-1.DATA.xml", "tvshowhub.DATA.xml"):
            recent = next(
                item for item in shortcuts(shortcut_file)
                if text(item, "label") == "Recently Added TV Shows"
            )
            action = text(recent, "action")
            self.assertIn("recentlyaddedtvshows.xsp", action)
            self.assertNotIn("episodes", action.casefold())

    def test_library_previews_end_with_a_full_library_link(self):
        for shortcut_file, label, media_type in (
            ("moviehub.DATA.xml", "Movies", "movies"),
            ("tvshowhub.DATA.xml", "TV Shows", "tvshows"),
        ):
            preview = next(
                item for item in shortcuts(shortcut_file)
                if text(item, "label") == label
            )
            action = text(preview, "action")
            self.assertIn("action=libraryrow", action)
            self.assertIn("mediatype=%s" % media_type, action)
            self.assertIn("limit=15", action)
            self.assertEqual(text(preview, "property[@name='widgetLimit']"), "")

    def test_navigation_contains_no_instance_specific_identifiers(self):
        managed = [
            "mainmenu.DATA.xml",
            "10000-1.DATA.xml",
            "moviehub.DATA.xml",
            "tvshowhub.DATA.xml",
            "htpc.properties.json",
        ]
        source = "\n".join(
            (SHORTCUTS_ROOT / name).read_text(encoding="utf-8")
            for name in managed
        )
        self.assertNotIn("jellyfintvshows", source.casefold())
        self.assertNotIn("library://video/jellyfin", source.casefold())
        self.assertIsNone(re.search(r"\b[0-9a-f]{32}\b", source, re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
