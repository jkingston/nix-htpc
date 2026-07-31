from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path


BINGIE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(
    os.environ.get("BINGIE_TOOLS_ROOT", str(BINGIE_ROOT / "tools"))
).resolve()
sys.path.insert(0, str(TOOLS_ROOT))

import generate_preview_anchors as anchors  # noqa: E402


def _canonical_xml_source() -> str:
    return "".join(
        (
            "<includes>\n",
            '\t<include name="HTPCPlaybackPresentationSlot">\n',
            "\t\t<definition>\n",
            '\t\t\t<control type="group">\n',
            f"\t\t\t\t<description>{anchors.SLOT_DESCRIPTION}</description>\n",
            '\t\t\t\t<control type="group">\n',
            (
                f"\t\t\t\t\t<description>"
                f"{anchors.PREVIEW_DESCRIPTION}</description>\n"
            ),
            anchors.GENERATED_BEGIN,
            anchors.render_animations(),
            anchors.GENERATED_END,
            "\n\t\t\t\t</control>\n",
            "\t\t\t</control>\n",
            "\t\t</definition>\n",
            "\t</include>\n",
            "</includes>\n",
        )
    )


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
        first = anchors.render_animations()
        second = anchors.render_animations()
        self.assertEqual(first, second)
        self.assertEqual(first.count("<animation "), 101)
        self.assertEqual(
            hashlib.sha256(first.encode("utf-8")).hexdigest(),
            "1b337f6f5f455f021b734d71040bf3ae6a4e623814f3c9e25abf30fb304b22cc",
        )
        self.assertIn(
            "$PARAM[property_prefix].$PARAM[slot].previewanchor",
            first,
        )

    def test_invalid_generator_inputs_fail_closed(self):
        for invalid in (-1, 101):
            with self.assertRaises(ValueError):
                anchors.anchor_offset(invalid)
        with self.assertRaises(ValueError):
            anchors.anchor_offset(50, 0)

    def test_update_is_idempotent_and_rejects_ambiguous_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            xml_path = Path(directory) / "playback.xml"
            source = (
                "<includes>\n"
                + anchors.GENERATED_BEGIN
                + "\t\t\t\tstale\n"
                + anchors.GENERATED_END
                + "\n</includes>\n"
            )
            xml_path.write_text(source, encoding="utf-8")

            anchors.update_file(xml_path)
            first = xml_path.read_text(encoding="utf-8")
            anchors.update_file(xml_path)
            self.assertEqual(xml_path.read_text(encoding="utf-8"), first)
            self.assertEqual(first.count("<animation "), 101)

            xml_path.write_text(source + source, encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "expected exactly one generated preview-anchor section",
            ):
                anchors.update_file(xml_path)

    def test_check_and_update_share_fail_closed_marker_validation(self):
        source = _canonical_xml_source()
        with tempfile.TemporaryDirectory() as directory:
            canonical_path = Path(directory) / "canonical.xml"
            canonical_path.write_text(source, encoding="utf-8")
            self.assertEqual(anchors.check_file(canonical_path), ())

        invalid_sources = {
            "missing": source.replace(anchors.GENERATED_BEGIN, ""),
            "duplicate": source.replace(
                anchors.GENERATED_BEGIN,
                anchors.GENERATED_BEGIN + anchors.GENERATED_BEGIN,
            ),
            "reversed": source.replace(
                anchors.GENERATED_BEGIN,
                "__GENERATED_BEGIN__",
            )
            .replace(anchors.GENERATED_END, anchors.GENERATED_BEGIN)
            .replace("__GENERATED_BEGIN__", anchors.GENERATED_END),
        }
        with tempfile.TemporaryDirectory() as directory:
            xml_path = Path(directory) / "playback.xml"
            for case, invalid_source in invalid_sources.items():
                with self.subTest(case=case):
                    xml_path.write_text(invalid_source, encoding="utf-8")
                    check_error = anchors.check_file(xml_path)
                    self.assertEqual(len(check_error), 1)
                    with self.assertRaisesRegex(
                        ValueError,
                        re.escape(check_error[0]),
                    ):
                        anchors.update_file(xml_path)


if __name__ == "__main__":
    unittest.main()
