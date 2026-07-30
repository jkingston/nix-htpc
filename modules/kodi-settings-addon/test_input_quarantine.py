from __future__ import absolute_import, division, print_function

import unittest

from input_quarantine import InputQuarantine, canonical_physical_key
from seek_controller import HOLD_ONSET_MAX, HOLD_RELEASE_IDLE


class CanonicalPhysicalKeyTest(unittest.TestCase):
    def test_cross_window_routes_share_one_physical_key(self):
        cases = (
            ("left", {}, "left"),
            ("timeline-left", {}, "left"),
            ("right", {}, "right"),
            ("timeline-right", {}, "right"),
            (
                "chapter-focus",
                {"physical_direction": "left"},
                "left",
            ),
            (
                "chapter-focus",
                {"physical_direction": "right"},
                "right",
            ),
            ("primary", {}, "select"),
            ("osd-primary", {}, "select"),
            ("chapter-select", {}, "select"),
            ("fullscreen-back", {}, "back"),
            ("osd-back", {}, "back"),
            ("chapter-exit", {"arm_back": True}, "back"),
        )
        for action, payload, expected in cases:
            with self.subTest(action=action):
                self.assertEqual(
                    canonical_physical_key(action, payload),
                    expected,
                )

    def test_synthetic_callbacks_have_no_physical_key(self):
        cases = (
            ("timeline-confirm", {}),
            ("timeline-cancel", {}),
            ("chapter-focus", {}),
            ("chapter-focus", {"physical_direction": "invalid"}),
            ("chapter-open", {}),
            ("chapter-exit", {"arm_back": False}),
            ("chapter-exit", {}),
        )
        for action, payload in cases:
            with self.subTest(action=action):
                self.assertIsNone(
                    canonical_physical_key(action, payload)
                )


class InputQuarantineTest(unittest.TestCase):
    def setUp(self):
        self.quarantine = InputQuarantine()

    def test_normal_events_pass_and_last_seen_never_moves_backwards(self):
        self.assertFalse(self.quarantine.should_suppress("left", 10.0))
        self.assertFalse(self.quarantine.should_suppress("left", 9.0))
        self.assertEqual(self.quarantine.last_seen, {"left": 10.0})

    def test_boundary_arms_only_recently_seen_keys(self):
        self.quarantine.should_suppress("left", 1.0)
        self.quarantine.should_suppress("right", 0.0)

        self.quarantine.on_playback_boundary(1.4)

        self.assertAlmostEqual(
            self.quarantine.deadlines["left"],
            max(
                1.0 + HOLD_ONSET_MAX,
                1.4 + HOLD_RELEASE_IDLE,
            ),
        )
        self.assertNotIn("right", self.quarantine.deadlines)
        self.assertNotIn("select", self.quarantine.deadlines)
        self.assertFalse(
            self.quarantine.should_suppress("select", 1.41)
        )

    def test_boundary_release_floor_covers_onset_window_gap(self):
        self.quarantine.should_suppress("right", 0.0)

        self.quarantine.on_playback_boundary(0.510)

        self.assertAlmostEqual(
            self.quarantine.deadlines["right"],
            max(
                0.0 + HOLD_ONSET_MAX,
                0.510 + HOLD_RELEASE_IDLE,
            ),
        )
        self.assertTrue(
            self.quarantine.should_suppress("right", 0.616)
        )

    def test_same_key_continuations_extend_until_release_quiet(self):
        self.quarantine.should_suppress("left", 1.0)
        self.quarantine.on_playback_boundary(1.1)
        onset_deadline = 1.0 + HOLD_ONSET_MAX

        self.assertTrue(
            self.quarantine.should_suppress("left", onset_deadline)
        )
        first_release = onset_deadline + HOLD_RELEASE_IDLE
        self.assertAlmostEqual(
            self.quarantine.deadlines["left"],
            first_release,
        )

        continuation = first_release - 0.01
        self.assertTrue(
            self.quarantine.should_suppress("left", continuation)
        )
        extended_release = continuation + HOLD_RELEASE_IDLE
        self.assertAlmostEqual(
            self.quarantine.deadlines["left"],
            extended_release,
        )

        fresh = extended_release + 0.001
        self.assertFalse(self.quarantine.should_suppress("left", fresh))
        self.assertNotIn("left", self.quarantine.deadlines)
        self.assertEqual(self.quarantine.last_seen["left"], fresh)

    def test_opposite_key_passes_while_original_key_remains_armed(self):
        self.quarantine.should_suppress("left", 1.0)
        self.quarantine.on_playback_boundary(1.1)

        self.assertFalse(self.quarantine.should_suppress("right", 1.2))
        self.assertIn("left", self.quarantine.deadlines)
        self.assertEqual(self.quarantine.last_seen["right"], 1.2)

    def test_only_latest_direction_arms_at_a_boundary(self):
        self.quarantine.should_suppress("left", 1.0)
        self.quarantine.should_suppress("right", 1.1)

        self.quarantine.on_playback_boundary(1.2)

        self.assertNotIn("left", self.quarantine.deadlines)
        self.assertIn("right", self.quarantine.deadlines)
        self.assertFalse(self.quarantine.should_suppress("left", 1.21))
        self.assertTrue(self.quarantine.should_suppress("right", 1.21))

    def test_select_and_back_arm_independently_of_latest_direction(self):
        self.quarantine.should_suppress("left", 1.0)
        self.quarantine.should_suppress("right", 1.1)
        self.quarantine.should_suppress("select", 1.12)
        self.quarantine.should_suppress("back", 1.14)

        self.quarantine.on_playback_boundary(1.2)

        self.assertNotIn("left", self.quarantine.deadlines)
        self.assertIn("right", self.quarantine.deadlines)
        self.assertIn("select", self.quarantine.deadlines)
        self.assertIn("back", self.quarantine.deadlines)

    def test_boundary_merges_purged_ingress_evidence(self):
        self.quarantine.on_playback_boundary(
            0.510,
            {
                "last_seen": {"right": 0.4},
                "latest_direction": {
                    "key": "right",
                    "timestamp": 0.4,
                },
            },
        )

        self.assertEqual(self.quarantine.last_seen["right"], 0.4)
        self.assertTrue(
            self.quarantine.should_suppress("right", 0.616)
        )

    def test_malformed_watermark_is_ignored(self):
        self.quarantine.merge_watermark(
            {
                "last_seen": "not-a-map",
                "latest_direction": {
                    "key": "invalid",
                    "timestamp": "not-a-number",
                },
            },
        )

        self.assertEqual(self.quarantine.last_seen, {})
        self.assertIsNone(self.quarantine.latest_direction)

    def test_repeated_and_out_of_order_boundaries_never_shorten(self):
        self.quarantine.should_suppress("left", 10.0)
        self.quarantine.on_playback_boundary(10.1)
        original = self.quarantine.deadlines["left"]

        self.quarantine.on_playback_boundary(9.9)
        self.assertEqual(self.quarantine.deadlines["left"], original)

        self.quarantine.should_suppress("left", 10.2)
        self.quarantine.on_playback_boundary(10.3)
        extended = self.quarantine.deadlines["left"]
        self.assertGreaterEqual(extended, original)

        self.quarantine.on_playback_boundary(20.0)
        self.assertEqual(self.quarantine.deadlines["left"], extended)

    def test_clear_erases_evidence_and_quarantine(self):
        self.quarantine.should_suppress("left", 1.0)
        self.quarantine.on_playback_boundary(1.1)

        self.quarantine.clear()

        self.assertEqual(self.quarantine.last_seen, {})
        self.assertEqual(self.quarantine.deadlines, {})


if __name__ == "__main__":
    unittest.main()
