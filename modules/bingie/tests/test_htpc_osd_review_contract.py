from __future__ import annotations

import hashlib
import os
import re
import struct
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


BINGIE_ROOT = Path(__file__).resolve().parents[1]
SKIN_ROOT = Path(
    os.environ.get("BINGIE_SKIN_ROOT", str(BINGIE_ROOT / "src"))
).resolve()
XML_ROOT = SKIN_ROOT / "1080i"
REVIEW_XML = XML_ROOT / "Custom_1192_HTPCVideoOSDReview.xml"
REVIEW_ASSETS = SKIN_ROOT / "resources" / "review"
VIDEO_OSD_XML = XML_ROOT / "IncludesHTPCVideoOSD.xml"
OSD_ICON_ASSETS = SKIN_ROOT / "resources" / "htpc" / "osd"

EXPECTED_FOCUS = {
    "transport-playing": "9201",
    "transport-paused": "9201",
    "timeline-playing": "9300",
    "timeline-idle": "9300",
    "timeline-chapters": "9300",
    "seek-backward": "9300",
    "seek-forward": "9300",
    "seek-forward-modal": "9300",
    "seek-forward-loading": "9300",
    "seek-forward-unavailable": "9300",
    "seek-forward-slot-b": "9300",
    "top-stop": "9101",
}
EXPECTED_CLEANUP = {
    "htpc.review.ready",
    "htpc.review.scenario",
    "htpc.review.revision",
    "htpc.review.paused",
    "htpc.review.title",
    "htpc.review.subtitle",
    "htpc.review.elapsed",
    "htpc.review.remaining",
    "htpc.review.seek.actualmarker",
    "htpc.review.seek.modal",
    "htpc.review.seek.viewactive",
    "htpc.review.seek.viewslot",
    "htpc.review.seek.a.targetvalid",
    "htpc.review.seek.a.targetfill",
    "htpc.review.seek.a.targetmarker",
    "htpc.review.seek.a.time",
    "htpc.review.seek.a.delta",
    "htpc.review.seek.a.prompt",
    "htpc.review.seek.a.previewstatus",
    "htpc.review.seek.a.previewpath",
    "htpc.review.seek.a.previewanchor",
    "htpc.review.seek.b.targetvalid",
    "htpc.review.seek.b.targetfill",
    "htpc.review.seek.b.targetmarker",
    "htpc.review.seek.b.time",
    "htpc.review.seek.b.delta",
    "htpc.review.seek.b.prompt",
    "htpc.review.seek.b.previewstatus",
    "htpc.review.seek.b.previewpath",
    "htpc.review.seek.b.previewanchor",
}


def _description(control: ET.Element) -> str:
    return control.findtext("description", default="").strip()


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as source:
        header = source.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", header[16:24])


def _png_bit_depth_and_color_type(path: Path) -> tuple[int, int]:
    with path.open("rb") as source:
        header = source.read(26)
    if len(header) != 26 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    return header[24], header[25]


class HeadlessOsdReviewWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not REVIEW_XML.is_file():
            raise unittest.SkipTest("headless OSD review window is absent")
        cls.root = ET.parse(REVIEW_XML).getroot()

    def test_window_id_type_and_focus_scenarios_are_explicit(self):
        self.assertEqual(self.root.get("id"), "1192")
        self.assertEqual(self.root.get("type"), "dialog")
        self.assertEqual(
            self.root.findtext("defaultcontrol", default="").strip(),
            "9300",
        )

        actual = {}
        for action in self.root.findall("onload"):
            condition = action.get("condition", "")
            match = re.fullmatch(
                r"String\.IsEqual\(Window\(Home\)\.Property"
                r"\(htpc\.review\.scenario\),([a-z-]+)\)",
                condition,
            )
            self.assertIsNotNone(match, condition)
            focus = re.fullmatch(
                r"SetFocus\(([0-9]+)\)",
                (action.text or "").strip(),
            )
            self.assertIsNotNone(focus, action.text)
            actual[match.group(1)] = focus.group(1)
        self.assertEqual(actual, EXPECTED_FOCUS)

    def test_custom_window_id_is_unique(self):
        owners = []
        for source in sorted(XML_ROOT.glob("Custom_*.xml")):
            text = source.read_text(
                encoding="utf-8-sig",
                errors="replace",
            )
            match = re.search(r"<window\b[^>]*\bid=[\"']1192[\"']", text)
            if match:
                owners.append(source.name)
        self.assertEqual(owners, [REVIEW_XML.name])

    def test_opaque_backdrop_precedes_one_owned_osd(self):
        controls = self.root.find("controls")
        self.assertIsNotNone(controls)
        children = list(controls)
        self.assertEqual(children[0].tag, "control")
        self.assertEqual(
            _description(children[0]),
            "HTPC OSD review opaque backdrop",
        )
        self.assertEqual(children[0].findtext("width"), "1920")
        self.assertEqual(children[0].findtext("height"), "1080")
        backdrop = children[0].find("texture")
        self.assertIsNotNone(backdrop)
        self.assertTrue(backdrop.get("colordiffuse", "").startswith("ff"))

        owned = [
            node
            for node in controls.findall("include")
            if node.get("content") == "HTPCVideoOSD"
        ]
        self.assertEqual(len(owned), 1)
        self.assertGreater(children.index(owned[0]), 0)

    def test_owned_osd_review_remains_isolated_and_inert_after_cutover(self):
        include = next(
            node
            for node in self.root.iter("include")
            if node.get("content") == "HTPCVideoOSD"
        )
        parameters = {
            node.get("name"): node.get("value")
            for node in include.findall("param")
        }
        self.assertEqual(parameters["visible"], "true")
        self.assertEqual(parameters["property_window"], "Home")
        self.assertEqual(parameters["property_prefix"], "htpc.review.seek")
        self.assertEqual(parameters["production_actions"], "false")
        self.assertEqual(parameters["inert_actions"], "true")
        self.assertEqual(parameters["seekable_condition"], "true")
        self.assertEqual(parameters["preview_background_load"], "false")
        self.assertEqual(
            parameters["chapter_available_condition"],
            "String.IsEqual(Window(Home).Property("
            "htpc.review.scenario),timeline-chapters)",
        )
        self.assertEqual(
            parameters["timeline_chapter_hint_label"],
            "↑  Chapters",
        )
        self.assertEqual(
            parameters["preview_loading_label"],
            "Loading preview…",
        )
        self.assertEqual(
            parameters["preview_unavailable_label"],
            "Preview unavailable",
        )
        self.assertEqual(
            parameters["presentation_ready"],
            "true",
        )
        self.assertEqual(
            parameters["view_inactive_condition"],
            "String.IsEmpty(Window(Home).Property("
            "htpc.review.seek.viewactive))",
        )
        self.assertEqual(
            parameters["modal_condition"],
            "!String.IsEmpty(Window(Home).Property("
            "htpc.review.seek.modal))",
        )
        for parameter in (
            "title",
            "subtitle",
            "elapsed",
            "remaining",
            "paused_condition",
        ):
            with self.subTest(parameter=parameter):
                self.assertIn("htpc.review.", parameters[parameter])
        progress = {
            "buffer_progress": ("70", "Integer.ValueOf(70)"),
            "actual_progress": ("40", "Integer.ValueOf(40)"),
        }
        for parameter, (percentage, expression) in progress.items():
            with self.subTest(parameter=parameter):
                self.assertEqual(parameters[parameter], expression)
                match = re.fullmatch(
                    r"Integer\.ValueOf\(([0-9]{1,3})\)",
                    expression,
                )
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), percentage)
                self.assertIn(int(percentage), range(101))
                self.assertNotIn("Window(", expression)
                self.assertNotIn("$INFO", expression)

    def test_review_transport_state_maps_to_the_owned_toggle_textures(self):
        include = next(
            node
            for node in self.root.iter("include")
            if node.get("content") == "HTPCVideoOSD"
        )
        parameters = {
            node.get("name"): node.get("value")
            for node in include.findall("param")
        }
        self.assertEqual(
            parameters["paused_condition"],
            "!String.IsEmpty(Window(Home).Property(htpc.review.paused))",
        )

        osd_root = ET.parse(VIDEO_OSD_XML).getroot()
        transports = [
            control
            for control in osd_root.iter("control")
            if control.get("id") == "9201"
        ]
        self.assertEqual(len(transports), 1)
        transport = transports[0]
        self.assertEqual(transport.get("type"), "togglebutton")
        self.assertEqual(
            (transport.findtext("usealttexture") or "").strip(),
            "$PARAM[paused_condition]",
        )
        expected = {
            "texturefocus": "osd/bingie/pause_fo.png",
            "texturenofocus": "osd/bingie/pause.png",
            "alttexturefocus": "osd/bingie/play_fo.png",
            "alttexturenofocus": "osd/bingie/play.png",
        }
        self.assertEqual(
            {
                name: (transport.findtext(name) or "").strip()
                for name in expected
            },
            expected,
        )

    def test_window_has_no_media_or_device_side_effect(self):
        serialized = ET.tostring(self.root, encoding="unicode")
        forbidden = (
            "ActivateWindow(",
            "CEC",
            "DPMS",
            "NotifyAll(",
            "PlayMedia(",
            "PlayerControl(",
            "SetFocus(203)",
            "StepBack",
            "StepForward",
            "VideoWindow",
            "htpc.seek.",
            "htpc.service.",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, serialized)

    def test_unload_clears_the_complete_fixture_namespace(self):
        cleared = set()
        for action in self.root.findall("onunload"):
            match = re.fullmatch(
                r"ClearProperty\((htpc\.review\.[a-z.]+),Home\)",
                (action.text or "").strip(),
            )
            self.assertIsNotNone(match, action.text)
            cleared.add(match.group(1))
        self.assertEqual(cleared, EXPECTED_CLEANUP)

    def test_review_frames_are_deterministic_16_by_9_pngs(self):
        expected_hashes = {
            "seek-25.png": (
                "e141f0578d5ccd097ae7f263d748c031"
                "99ea10c92eb4eb8360726f7b64e99a41"
            ),
            "seek-75.png": (
                "29464ab1baa9748478b8120cac5cc417"
                "34f5a219477d74d191c4209032f7d376"
            ),
        }
        for filename, expected_hash in expected_hashes.items():
            path = REVIEW_ASSETS / filename
            with self.subTest(filename=filename):
                self.assertEqual(_png_dimensions(path), (380, 214))
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    expected_hash,
                )
                self.assertTrue(path.with_suffix(".svg").is_file())
        self.assertTrue((REVIEW_ASSETS / "README.md").is_file())

    def test_top_stop_uses_deterministic_fork_owned_icons(self):
        osd_root = ET.parse(VIDEO_OSD_XML).getroot()
        stop_controls = [
            control
            for control in osd_root.iter("control")
            if control.get("id") == "9101"
        ]
        self.assertEqual(len(stop_controls), 1)
        stop = stop_controls[0]
        expected = {
            "texturefocus": (
                "special://skin/resources/htpc/osd/stop-focused.png"
            ),
            "texturenofocus": (
                "special://skin/resources/htpc/osd/stop.png"
            ),
        }
        expected_hashes = {
            "stop.png": (
                "5745cc4c7b70783973452a2025f89c70"
                "09b073cc3b6a9dafb7f301af4ffe6c93"
            ),
            "stop-focused.png": (
                "b676fc905f7e732db1c98b6966367bf5"
                "281691e7a2d01b2e89f71fd69240880f"
            ),
        }
        for texture_name, expected_path in expected.items():
            texture = stop.find(texture_name)
            self.assertIsNotNone(texture)
            self.assertEqual((texture.text or "").strip(), expected_path)
        self.assertEqual(
            stop.find("texturefocus").get("colordiffuse"),
            "ffffffff",
        )
        self.assertEqual(
            stop.find("texturenofocus").get("colordiffuse"),
            "ccffffff",
        )

        for filename, expected_hash in expected_hashes.items():
            path = OSD_ICON_ASSETS / filename
            with self.subTest(filename=filename):
                self.assertEqual(_png_dimensions(path), (68, 68))
                self.assertEqual(
                    _png_bit_depth_and_color_type(path),
                    (8, 6),
                    "runtime OSD icons must be 8-bit RGBA PNGs",
                )
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    expected_hash,
                )
                self.assertTrue(path.with_suffix(".svg").is_file())
        self.assertTrue((OSD_ICON_ASSETS / "README.md").is_file())

        namespace = {"svg": "http://www.w3.org/2000/svg"}
        idle_source = ET.parse(OSD_ICON_ASSETS / "stop.svg").getroot()
        idle_glyph = idle_source.find("svg:rect", namespace)
        self.assertIsNotNone(idle_glyph)
        self.assertEqual(
            {
                attribute: idle_glyph.get(attribute)
                for attribute in ("x", "y", "width", "height")
            },
            {"x": "18", "y": "18", "width": "32", "height": "32"},
        )

        focused_source = ET.parse(
            OSD_ICON_ASSETS / "stop-focused.svg"
        ).getroot()
        focus_disc = focused_source.find("svg:circle", namespace)
        focus_glyph = focused_source.find("svg:rect", namespace)
        self.assertIsNotNone(focus_disc)
        self.assertIsNotNone(focus_glyph)
        self.assertEqual(
            {
                attribute: focus_disc.get(attribute)
                for attribute in ("cx", "cy", "r")
            },
            {"cx": "34", "cy": "34", "r": "33"},
        )
        self.assertEqual(
            {
                attribute: focus_glyph.get(attribute)
                for attribute in ("x", "y", "width", "height")
            },
            {"x": "19", "y": "19", "width": "30", "height": "30"},
        )


if __name__ == "__main__":
    unittest.main()
