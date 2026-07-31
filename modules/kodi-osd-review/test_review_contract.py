from __future__ import annotations

import os
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from review_contract import (
    CLEANUP_PROPERTY_KEYS,
    CURRENT_PROPERTY_KEYS,
    EXPECTED_FOCUS,
    RETIRED_PROPERTY_KEYS,
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
                self.assertTrue(set(values).issubset(CURRENT_PROPERTY_KEYS))
                self.assertTrue(
                    all(key.startswith("htpc.review.") for key in values)
                )

    def test_every_scenario_has_one_deterministic_actual_marker(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                values = scenario_properties(scenario)
                self.assertEqual(
                    values["htpc.review.seek.actualmarker"],
                    "40,40",
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

    def test_modal_forward_seek_differs_only_by_modal_fence(self):
        nonmodal = scenario_properties("seek-forward")
        modal = scenario_properties("seek-forward-modal")
        self.assertNotIn("htpc.review.seek.modal", nonmodal)
        self.assertEqual(modal["htpc.review.seek.modal"], "true")
        ignored = {
            "htpc.review.scenario",
            "htpc.review.seek.modal",
        }
        self.assertEqual(
            {
                key: value
                for key, value in nonmodal.items()
                if key not in ignored
            },
            {
                key: value
                for key, value in modal.items()
                if key not in ignored
            },
        )

    def test_forward_preview_states_change_only_status_and_path(self):
        ready = scenario_properties("seek-forward")
        states = {
            "seek-forward-loading": "loading",
            "seek-forward-unavailable": "unavailable",
        }
        ignored = {
            "htpc.review.scenario",
            "htpc.review.seek.a.previewstatus",
            "htpc.review.seek.a.previewpath",
        }
        baseline = {
            key: value
            for key, value in ready.items()
            if key not in ignored
        }
        self.assertEqual(
            ready["htpc.review.seek.a.previewstatus"],
            "ready",
        )
        self.assertTrue(ready["htpc.review.seek.a.previewpath"])
        for scenario, status in states.items():
            with self.subTest(scenario=scenario):
                values = scenario_properties(scenario)
                self.assertEqual(
                    values["htpc.review.seek.a.previewstatus"],
                    status,
                )
                self.assertEqual(
                    values["htpc.review.seek.a.previewpath"],
                    "",
                )
                self.assertEqual(
                    {
                        key: value
                        for key, value in values.items()
                        if key not in ignored
                    },
                    baseline,
                )

    def test_non_seek_scenarios_publish_only_the_actual_seek_marker(self):
        for scenario in (
            "transport-playing",
            "transport-paused",
            "timeline-playing",
            "timeline-idle",
            "timeline-chapters",
            "top-stop",
        ):
            values = scenario_properties(scenario)
            self.assertEqual(
                {
                    key: value
                    for key, value in values.items()
                    if key.startswith("htpc.review.seek.")
                },
                {"htpc.review.seek.actualmarker": "40,40"},
            )

    def test_playing_fixtures_differ_only_by_playback_state(self):
        playing = scenario_properties("transport-playing")
        paused = scenario_properties("transport-paused")
        self.assertEqual(playing["htpc.review.paused"], "")
        self.assertEqual(paused["htpc.review.paused"], "true")
        ignored = {
            "htpc.review.scenario",
            "htpc.review.paused",
        }
        self.assertEqual(
            {
                key: value
                for key, value in playing.items()
                if key not in ignored
            },
            {
                key: value
                for key, value in paused.items()
                if key not in ignored
            },
        )
        timeline = scenario_properties("timeline-playing")
        self.assertEqual(timeline["htpc.review.paused"], "")
        self.assertEqual(
            {
                key: value
                for key, value in timeline.items()
                if key not in ignored
            },
            {
                key: value
                for key, value in paused.items()
                if key not in ignored
            },
        )

    def test_chapter_hint_is_window_owned_not_staged_state(self):
        idle = scenario_properties("timeline-idle")
        chapters = scenario_properties("timeline-chapters")
        self.assertEqual(
            {
                key: value
                for key, value in idle.items()
                if key != "htpc.review.scenario"
            },
            {
                key: value
                for key, value in chapters.items()
                if key != "htpc.review.scenario"
            },
        )
        self.assertNotIn("htpc.review.focuscue", CURRENT_PROPERTY_KEYS)

        root = ET.parse(REVIEW_WINDOW).getroot()
        include = next(
            node
            for node in root.iter("include")
            if node.get("content") == "HTPCVideoOSD"
        )
        parameters = {
            node.get("name"): node.get("value")
            for node in include.findall("param")
        }
        self.assertEqual(
            parameters["chapter_available_condition"],
            "String.IsEqual(Window(Home).Property("
            "htpc.review.scenario),timeline-chapters)",
        )
        self.assertEqual(
            parameters["timeline_chapter_hint_label"],
            "↑  Chapters",
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
        self.assertEqual(set(cleared), set(CLEANUP_PROPERTY_KEYS))

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

    def test_fixture_revision_two_matches_current_and_retired_property_sets(
        self,
    ):
        self.assertEqual(
            RETIRED_PROPERTY_KEYS,
            (
                "htpc.review.seek.a.revision",
                "htpc.review.seek.a.phase",
                "htpc.review.seek.b.revision",
                "htpc.review.seek.b.phase",
            ),
        )
        self.assertTrue(
            set(CURRENT_PROPERTY_KEYS).isdisjoint(RETIRED_PROPERTY_KEYS)
        )
        for scenario in SCENARIOS:
            values = scenario_properties(scenario)
            self.assertEqual(values["htpc.review.revision"], "2")
            self.assertTrue(set(values).isdisjoint(RETIRED_PROPERTY_KEYS))

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
