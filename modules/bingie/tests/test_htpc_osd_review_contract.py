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

EXPECTED_FOCUS = {
    "transport-paused": "9201",
    "timeline-idle": "9300",
    "timeline-chapters": "9300",
    "seek-backward": "9300",
    "seek-forward": "9300",
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
    "htpc.review.focuscue",
    "htpc.review.seek.viewactive",
    "htpc.review.seek.viewslot",
    "htpc.review.seek.a.revision",
    "htpc.review.seek.a.phase",
    "htpc.review.seek.a.targetvalid",
    "htpc.review.seek.a.targetfill",
    "htpc.review.seek.a.targetmarker",
    "htpc.review.seek.a.time",
    "htpc.review.seek.a.delta",
    "htpc.review.seek.a.prompt",
    "htpc.review.seek.a.previewstatus",
    "htpc.review.seek.a.previewpath",
    "htpc.review.seek.a.previewanchor",
}


def _description(control: ET.Element) -> str:
    return control.findtext("description", default="").strip()


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as source:
        header = source.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", header[16:24])


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
            "9201",
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

    def test_owned_osd_uses_isolated_data_and_inert_actions(self):
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
        self.assertEqual(parameters["preview_background_load"], "false")
        self.assertEqual(
            parameters["timeline_focus_cue_label"],
            "$INFO[Window(Home).Property(htpc.review.focuscue)]",
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


if __name__ == "__main__":
    unittest.main()
