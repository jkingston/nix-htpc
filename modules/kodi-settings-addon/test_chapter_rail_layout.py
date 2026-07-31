import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


CHAPTER_RAIL = Path(__file__).resolve().parent / (
    "resources/skins/Default/1080i/ChapterRail.xml"
)


class ChapterRailLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ET.parse(CHAPTER_RAIL).getroot()
        panels = cls.root.findall(".//control[@type='panel']")
        if len(panels) != 1:
            raise AssertionError("expected exactly one chapter panel")
        cls.panel = panels[0]

    @staticmethod
    def _geometry(node):
        names = ("left", "top", "width", "height")
        return tuple(int(node.findtext(name)) for name in names)

    def test_panel_focus_and_navigation_contract_is_unchanged(self):
        default = self.root.find("defaultcontrol")
        self.assertEqual((default.text, default.get("always")), ("11", "true"))
        self.assertEqual(self.panel.get("id"), "11")
        expected = {
            "orientation": "horizontal",
            "onleft": "11",
            "onright": "11",
            "pagecontrol": "60",
            "scrolltime": "160",
            "preloaditems": "2",
        }
        actual = {name: self.panel.findtext(name) for name in expected}
        self.assertEqual(actual, expected)
        for handler in ("onup", "ondown", "onclick"):
            self.assertEqual(self.root.findall(".//" + handler), [])

    def test_visual_geometry_art_and_focus_are_exact(self):
        scrim, header = self.root.findall("./controls/control")[:2]
        self.assertEqual(self._geometry(scrim), (0, 0, 1920, 1080))
        self.assertEqual(scrim.find("texture").get("colordiffuse"), "44000000")
        self.assertEqual(self._geometry(header), (384, 638, 1152, 42))
        self.assertEqual(self._geometry(self.panel), (384, 690, 1152, 224))

        item = self.panel.find("itemlayout")
        focused = self.panel.find("focusedlayout")
        self.assertEqual(
            (item.get("width"), item.get("height")),
            (focused.get("width"), focused.get("height")),
        )
        self.assertEqual((item.get("width"), item.get("height")), ("288", "218"))
        item_thumb = item.find("control[@type='image']")
        focused_images = focused.findall("control[@type='image']")
        focused_thumb = focused_images[1]
        for thumb in (item_thumb, focused_thumb):
            self.assertEqual(self._geometry(thumb), (8, 8, 272, 153))
            width, height = self._geometry(thumb)[2:]
            self.assertEqual(width * 9, height * 16)
            self.assertEqual(thumb.findtext("aspectratio"), "scale")
            self.assertEqual(
                (thumb.find("texture").text or "").strip(),
                "$INFO[ListItem.Art(thumb)]",
            )

        outline = focused_images[0]
        self.assertEqual(self._geometry(outline), (2, 2, 284, 165))
        self.assertEqual(
            outline.find("texture").get("colordiffuse"),
            "$INFO[Skin.String(OSDBingieButtonsFocusColor)]",
        )
        self.assertNotIn(
            "f40612",
            ET.tostring(self.root, encoding="unicode").lower(),
        )


if __name__ == "__main__":
    unittest.main()
