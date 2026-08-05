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
SHORTCUT_TEMPLATE_XML = SKIN_ROOT / "shortcuts" / "template.xml"


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
        self.assertIsNotNone(
            widget.find("./control[@id='$PARAM[widgetid]']")
        )
        self.assertFalse(widget.findall(".//include[@content='WidgetSkeletonCards']"))
        header = self.include("widget_header_multi")
        skeleton_include = header.find(".//include[@content='WidgetSkeletonCards']")
        self.assertIsNotNone(skeleton_include)
        skeletons = self.include("WidgetSkeletonCards")
        self.assertFalse(skeletons.findall(".//control[@type='grouplist']"))
        self.assertFalse(skeletons.findall(".//onleft"))
        self.assertFalse(skeletons.findall(".//onright"))
        self.assertFalse(skeletons.findall(".//animation"))

    def test_generated_headers_receive_their_widget_style(self):
        template = ET.parse(SHORTCUT_TEMPLATE_XML).getroot()
        headers = template.findall(".//include[@content='widget_header_multi']")
        self.assertTrue(headers)
        for header in headers:
            styles = header.findall("./param[@name='widgetStyle']")
            self.assertEqual(len(styles), 1)
            self.assertEqual(
                styles[0].get("value"),
                "widget_layout_$SKINSHORTCUTS[widgetStyle]",
            )

    def test_row_header_has_no_continuously_animated_spinner(self):
        header = self.include("widget_header_multi")
        self.assertFalse(header.findall(".//multiimage"))

    def test_home_widgets_bound_background_art_preloading(self):
        values = [
            node.text for node in self.root.findall(".//preloaditems")
        ]
        self.assertTrue(values)
        self.assertNotIn("5", values)


if __name__ == "__main__":
    unittest.main()
