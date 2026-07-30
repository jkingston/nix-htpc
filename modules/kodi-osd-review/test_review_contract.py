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

    def test_non_seek_scenarios_publish_no_seek_view(self):
        for scenario in ("transport-paused", "timeline-idle", "top-stop"):
            values = scenario_properties(scenario)
            self.assertFalse(
                any(key.startswith("htpc.review.seek.") for key in values)
            )

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
        for scenario in ("seek-backward", "seek-forward"):
            uri = scenario_properties(scenario)[
                "htpc.review.seek.a.previewpath"
            ]
            prefix = "special://skin/"
            self.assertTrue(uri.startswith(prefix))
            relative = uri[len(prefix) :]
            self.assertNotIn("..", Path(relative).parts)
            self.assertTrue((SKIN_ROOT / relative).is_file())


if __name__ == "__main__":
    unittest.main()
