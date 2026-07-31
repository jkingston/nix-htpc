from __future__ import annotations

import os
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from review_contract import (
    EXPECTED_FOCUS,
    PROPERTY_KEYS,
    SCENARIOS,
    RequestError,
    parse_request,
    scenario_properties,
)

ROOT = Path(__file__).resolve().parent
REVIEW_WINDOW = Path(
    os.environ.get(
        "HTPC_OSD_REVIEW_WINDOW",
        str(
            ROOT.parent
            / "bingie/src/1080i/Custom_1192_HTPCVideoOSDReview.xml"
        ),
    )
)
SKIN_ROOT = Path(
    os.environ.get(
        "HTPC_OSD_REVIEW_SKIN_ROOT",
        str(ROOT.parent / "bingie/src"),
    )
)


class ReviewContractTest(unittest.TestCase):
    def test_request_is_one_whitelisted_literal(self):
        for scenario in SCENARIOS:
            self.assertEqual(
                parse_request(["state=" + scenario]),
                ("state", scenario),
            )
        self.assertEqual(
            parse_request(["command=close"]),
            ("command", "close"),
        )
        for invalid in (
            [],
            ["state=seek-forward", "extra=true"],
            ["state=unknown"],
            ["command=open"],
            ["state=seek-forward;PlayerControl(stop)"],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(RequestError):
                    parse_request(invalid)

    def test_every_scenario_is_complete_and_namespaced(self):
        ready = "htpc.review.ready"
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                values = scenario_properties(scenario)
                self.assertNotIn(ready, values)
                self.assertEqual(
                    values["htpc.review.scenario"],
                    scenario,
                )
                self.assertTrue(set(values).issubset(PROPERTY_KEYS))
                self.assertTrue(
                    all(key.startswith("htpc.review.") for key in values)
                )

    def test_seek_pair_proves_target_specific_preview_and_position(self):
        backward = scenario_properties("seek-backward")
        forward = scenario_properties("seek-forward")
        for field in (
            "targetfill",
            "targetmarker",
            "time",
            "delta",
            "previewpath",
            "previewanchor",
        ):
            with self.subTest(field=field):
                key = "htpc.review.seek.a." + field
                self.assertNotEqual(backward[key], forward[key])
        self.assertNotIn("htpc.review.actualprogress", backward)
        self.assertNotIn("htpc.review.actualprogress", forward)
        self.assertNotIn("htpc.review.bufferprogress", backward)
        self.assertNotIn("htpc.review.bufferprogress", forward)

    def test_forward_seek_is_identical_across_atomic_slots(self):
        slot_a = scenario_properties("seek-forward")
        slot_b = scenario_properties("seek-forward-slot-b")
        self.assertEqual(slot_a["htpc.review.seek.viewslot"], "a")
        self.assertEqual(slot_b["htpc.review.seek.viewslot"], "b")
        for field in (
            "revision",
            "phase",
            "targetvalid",
            "targetfill",
            "targetmarker",
            "time",
            "delta",
            "prompt",
            "previewstatus",
            "previewpath",
            "previewanchor",
        ):
            with self.subTest(field=field):
                self.assertEqual(
                    slot_a["htpc.review.seek.a." + field],
                    slot_b["htpc.review.seek.b." + field],
                )

    def test_non_seek_scenarios_publish_no_seek_view(self):
        for scenario in (
            "transport-paused",
            "timeline-idle",
            "timeline-chapters",
            "top-stop",
        ):
            values = scenario_properties(scenario)
            self.assertFalse(
                any(key.startswith("htpc.review.seek.") for key in values)
            )

    def test_chapter_focus_cue_preserves_seek_help(self):
        idle = scenario_properties("timeline-idle")[
            "htpc.review.focuscue"
        ]
        chapters = scenario_properties("timeline-chapters")[
            "htpc.review.focuscue"
        ]
        self.assertNotEqual(idle, chapters)
        for token in ("10s", "Hold to scrub"):
            with self.subTest(token=token):
                self.assertIn(token, idle)
                self.assertIn(token, chapters)
        self.assertNotIn("Chapters", idle)
        self.assertIn("↑  Chapters", chapters)

    def test_driver_keys_and_focus_equal_the_skin_contract(self):
        root = ET.parse(REVIEW_WINDOW).getroot()
        cleared = []
        for action in root.findall("onunload"):
            match = re.fullmatch(
                r"ClearProperty\((htpc\.review\.[a-z.]+),Home\)",
                (action.text or "").strip(),
            )
            self.assertIsNotNone(match, action.text)
            cleared.append(match.group(1))
        self.assertEqual(set(cleared), set(PROPERTY_KEYS))

        focus = {}
        for action in root.findall("onload"):
            scenario = re.search(r",([a-z-]+)\)$", action.get("condition", ""))
            control = re.fullmatch(
                r"SetFocus\(([0-9]+)\)",
                (action.text or "").strip(),
            )
            self.assertIsNotNone(scenario, action.get("condition"))
            self.assertIsNotNone(control, action.text)
            focus[scenario.group(1)] = control.group(1)
        self.assertEqual(focus, EXPECTED_FOCUS)

    def test_preview_paths_resolve_inside_the_packaged_skin(self):
        scenarios = (
            ("seek-backward", "a"),
            ("seek-forward", "a"),
            ("seek-forward-slot-b", "b"),
        )
        for scenario, slot in scenarios:
            uri = scenario_properties(scenario)[
                "htpc.review.seek." + slot + ".previewpath"
            ]
            prefix = "special://skin/"
            self.assertTrue(uri.startswith(prefix))
            relative = uri[len(prefix) :]
            self.assertNotIn("..", Path(relative).parts)
            self.assertTrue((SKIN_ROOT / relative).is_file())


if __name__ == "__main__":
    unittest.main()
