from __future__ import annotations

import os
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


SKIN_ROOT = Path(
    os.environ.get(
        "BINGIE_SKIN_ROOT",
        str(Path(__file__).resolve().parents[1] / "src"),
    )
)
WIDGETS_XML = SKIN_ROOT / "1080i" / "IncludesHomeWidgets.xml"


class WidgetSkeletonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ET.parse(WIDGETS_XML).getroot()

    def include(self, name):
        matches = self.root.findall("./include[@name='%s']" % name)
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_skeletons_cover_every_home_card_shape(self):
        skeletons = self.include("WidgetSkeletonCards")
        visibility = [
            node.text or "" for node in skeletons.findall(".//visible")
        ]
        for style in ("poster", "landscape", "square"):
            self.assertTrue(
                any("widget_layout_%s" % style in value for value in visibility)
            )
        self.assertIn(
            "Container($PARAM[widgetid]).IsUpdating",
            visibility[0],
        )
        self.assertIn("NumItems,0", visibility[0])

    def test_dynamic_widget_installs_skeleton_without_focusable_controls(self):
        widget = self.include("widget_base_normal")
        skeleton_include = widget.find("./include[@content='WidgetSkeletonCards']")
        self.assertIsNotNone(skeleton_include)
        skeletons = self.include("WidgetSkeletonCards")
        self.assertFalse(skeletons.findall(".//onleft"))
        self.assertFalse(skeletons.findall(".//onright"))
        self.assertFalse(skeletons.findall(".//animation"))

    def test_row_header_has_no_continuously_animated_spinner(self):
        header = self.include("widget_header_multi")
        self.assertFalse(header.findall(".//multiimage"))


if __name__ == "__main__":
    unittest.main()
