from __future__ import annotations

import hashlib
import os
import sys
import unittest
from pathlib import Path


BINGIE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(
    os.environ.get("BINGIE_TOOLS_ROOT", str(BINGIE_ROOT / "tools"))
).resolve()
sys.path.insert(0, str(TOOLS_ROOT))

import generate_preview_anchors as anchors  # noqa: E402


class PreviewAnchorGeneratorTest(unittest.TestCase):
    def test_anchor_domain_is_complete_monotonic_and_has_exact_endpoints(self):
        rows = anchors.anchor_rows()
        self.assertEqual(len(rows), 101)
        self.assertEqual([anchor for anchor, _offset in rows], list(range(101)))
        self.assertEqual(rows[0], (0, 0))
        self.assertEqual(rows[-1], (100, anchors.TIMELINE_WIDTH))

        offsets = [offset for _anchor, offset in rows]
        self.assertEqual(offsets, sorted(offsets))
        self.assertTrue(
            all(left < right for left, right in zip(offsets, offsets[1:]))
        )
        self.assertLessEqual(max(b - a for a, b in zip(offsets, offsets[1:])), 12)
        self.assertGreaterEqual(min(b - a for a, b in zip(offsets, offsets[1:])), 11)

    def test_rounding_is_integer_and_symmetric(self):
        for anchor in range(101):
            opposite = 100 - anchor
            self.assertEqual(
                anchors.anchor_offset(anchor)
                + anchors.anchor_offset(opposite),
                anchors.TIMELINE_WIDTH,
            )

    def test_generation_is_byte_deterministic(self):
        expected_hashes = {
            "a": "8a1fe6b56ebf8569e5c73a3720bb459beba76bfdde11f70b68bf03f785bd3729",
            "b": "f0dc6b020e9fa0871dafd55b8d9f2e3c9dc7348b43feb339b7480ec34f3c7726",
        }
        for slot in anchors.SLOTS:
            with self.subTest(slot=slot):
                first = anchors.render_animations(slot)
                second = anchors.render_animations(slot)
                self.assertEqual(first, second)
                self.assertEqual(first.count("<animation "), 101)
                self.assertEqual(
                    hashlib.sha256(first.encode("utf-8")).hexdigest(),
                    expected_hashes[slot],
                )

    def test_invalid_generator_inputs_fail_closed(self):
        for invalid in (-1, 101):
            with self.assertRaises(ValueError):
                anchors.anchor_offset(invalid)
        with self.assertRaises(ValueError):
            anchors.anchor_offset(50, 0)
        with self.assertRaises(ValueError):
            anchors.render_animations("not-a-slot")


if __name__ == "__main__":
    unittest.main()
