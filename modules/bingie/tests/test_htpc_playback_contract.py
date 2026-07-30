from __future__ import annotations

import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


BINGIE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
TOOLS_ROOT = Path(
    os.environ.get("BINGIE_TOOLS_ROOT", str(BINGIE_ROOT / "tools"))
).resolve()
DEFAULT_SKIN_ROOT = (BINGIE_ROOT / "src").resolve()
SKIN_ROOT = Path(
    os.environ.get("BINGIE_SKIN_ROOT", str(DEFAULT_SKIN_ROOT))
).resolve()
XML_ROOT = SKIN_ROOT / "1080i"
PLAYBACK_XML = XML_ROOT / "IncludesHTPCPlayback.xml"
INCLUDES_XML = XML_ROOT / "Includes.xml"
OSD_XML = XML_ROOT / "IncludesOSD.xml"
SETTINGS_ROOT = Path(
    os.environ.get(
        "HTPC_SETTINGS_ROOT",
        str(REPOSITORY_ROOT / "modules" / "kodi-settings-addon"),
    )
).resolve()
PRESENTER = SETTINGS_ROOT / "presenter.py"
MEDIA_CONTRACT = SETTINGS_ROOT / "media_contract.py"
UPSTREAM_ASSETS = BINGIE_ROOT / "upstream-assets.nix"

sys.path.insert(0, str(TOOLS_ROOT))

import generate_preview_anchors as anchors  # noqa: E402


TARGET_FILL_DESCRIPTION = "HTPC target progress fill"
TARGET_MARKER_DESCRIPTION = "HTPC target position marker"
CUT_MARKERS_DESCRIPTION = "HTPC cut markers"
CHAPTER_MARKERS_DESCRIPTION = "HTPC chapter markers"
PREVIEW_DESCRIPTION = anchors.PREVIEW_DESCRIPTION
SERVICE_READY = "Window(Home).Property(htpc.service.ready)"
VIEW_ACTIVE = "Window(Home).Property(htpc.seek.viewactive)"
VIEW_SLOT = "Window(Home).Property(htpc.seek.viewslot)"


def _slot_info(slot: str, field: str) -> str:
    return f"Window(Home).Property(htpc.seek.{slot}.{field})"


def _description(control: ET.Element) -> str:
    return control.findtext("description", default="").strip()


def _controls_by_description(root: ET.Element, description: str):
    return [
        control
        for control in root.iter("control")
        if _description(control) == description
    ]


def _visible_text(control: ET.Element) -> str:
    return " ".join(
        (node.text or "").strip() for node in control.findall("visible")
    )


def _literal_texture_paths(root: ET.Element):
    texture_tags = {
        "texture",
        "texturebg",
        "lefttexture",
        "midtexture",
        "righttexture",
        "texturesliderbar",
        "textureslidernib",
        "textureslidernibfocus",
        "textureprogress",
        "texturebackground",
    }
    for node in root.iter():
        if node.tag not in texture_tags:
            continue
        value = (node.text or "").strip()
        if not value or "$" in value or "://" in value:
            continue
        yield value


class ExpectedForkLayoutTest(unittest.TestCase):
    def test_expected_vendored_fork_layout_exists(self):
        missing = [
            path
            for path in (SKIN_ROOT, PLAYBACK_XML, INCLUDES_XML, OSD_XML)
            if not path.exists()
        ]
        self.assertEqual(
            missing,
            [],
            "the structural suite targets the planned vendored fork; "
            "missing: " + ", ".join(str(path) for path in missing),
        )


class PlaybackXmlContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = (PLAYBACK_XML, INCLUDES_XML, OSD_XML)
        if not all(path.is_file() for path in required):
            raise unittest.SkipTest(
                "planned modules/bingie/src fork is not present yet"
            )
        cls.playback_root = ET.parse(PLAYBACK_XML).getroot()
        cls.includes_root = ET.parse(INCLUDES_XML).getroot()
        cls.osd_root = ET.parse(OSD_XML).getroot()

    def _slot_group(self, slot: str) -> ET.Element:
        description = anchors.SLOT_DESCRIPTION.format(slot)
        matches = _controls_by_description(self.playback_root, description)
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one {description!r} control",
        )
        return matches[0]

    def _one_slot_control(
        self, slot: str, description: str
    ) -> ET.Element:
        matches = _controls_by_description(self._slot_group(slot), description)
        self.assertEqual(
            len(matches),
            1,
            f"slot {slot}: expected exactly one {description!r} control",
        )
        return matches[0]

    def test_local_playback_include_is_registered_once(self):
        registrations = [
            node
            for node in self.includes_root.iter("include")
            if node.get("file") == PLAYBACK_XML.name
        ]
        self.assertEqual(len(registrations), 1)

        definitions = [
            node
            for node in self.playback_root.findall("include")
            if node.get("name")
        ]
        self.assertEqual(
            len(definitions),
            1,
            "the local file should expose one reviewable presentation include",
        )
        include_name = definitions[0].get("name")
        consumers = [
            node
            for node in self.osd_root.iter("include")
            if (node.text or "").strip() == include_name
        ]
        self.assertEqual(
            len(consumers),
            1,
            f"{include_name!r} must be consumed exactly once by IncludesOSD.xml",
        )

    def test_two_presentation_slots_are_mutually_exclusive(self):
        all_slot_groups = [
            control
            for control in self.playback_root.iter("control")
            if _description(control).startswith(
                "HTPC playback presentation slot "
            )
        ]
        self.assertEqual(len(all_slot_groups), 2)

        for slot in anchors.SLOTS:
            with self.subTest(slot=slot):
                visible = _visible_text(self._slot_group(slot))
                self.assertIn(
                    f"!String.IsEmpty({SERVICE_READY})", visible
                )
                self.assertIn(f"!String.IsEmpty({VIEW_ACTIVE})", visible)
                self.assertIn(
                    f"String.IsEqual({VIEW_SLOT},{slot})", visible
                )
                other = "b" if slot == "a" else "a"
                self.assertNotIn(
                    f"String.IsEqual({VIEW_SLOT},{other})", visible
                )

    def test_target_fill_and_marker_are_ranges_controls(self):
        contracts = {
            TARGET_FILL_DESCRIPTION: "targetfill",
            TARGET_MARKER_DESCRIPTION: "targetmarker",
        }
        for slot in anchors.SLOTS:
            for description, field in contracts.items():
                with self.subTest(slot=slot, description=description):
                    control = self._one_slot_control(slot, description)
                    self.assertEqual(control.get("type"), "ranges")
                    self.assertEqual(
                        control.findtext("info", default="").strip(),
                        _slot_info(slot, field),
                    )

    def test_marker_layers_preserve_native_order_in_each_slot(self):
        expected = (
            TARGET_FILL_DESCRIPTION,
            CUT_MARKERS_DESCRIPTION,
            CHAPTER_MARKERS_DESCRIPTION,
            TARGET_MARKER_DESCRIPTION,
        )
        for slot in anchors.SLOTS:
            with self.subTest(slot=slot):
                descriptions = tuple(
                    _description(control)
                    for control in self._slot_group(slot).findall("control")
                    if _description(control) in expected
                )
                self.assertEqual(descriptions, expected)
                cut = self._one_slot_control(slot, CUT_MARKERS_DESCRIPTION)
                chapter = self._one_slot_control(
                    slot, CHAPTER_MARKERS_DESCRIPTION
                )
                self.assertEqual(cut.get("type"), "ranges")
                self.assertEqual(
                    cut.findtext("info", default="").strip(),
                    "Player.Cutlist",
                )
                self.assertEqual(chapter.get("type"), "ranges")
                self.assertEqual(
                    chapter.findtext("info", default="").strip(),
                    "Player.Chapters",
                )

    def test_no_target_window_property_is_bound_to_a_slider(self):
        violations = []
        for source_name, root in (
            (PLAYBACK_XML.name, self.playback_root),
            (OSD_XML.name, self.osd_root),
        ):
            for control in root.iter("control"):
                if control.get("type") != "slider":
                    continue
                info = control.findtext("info", default="").strip()
                is_seek_property = (
                    "Window(Home).Property(htpc.seek." in info
                )
                is_target = (
                    "target" in info.lower()
                    or "target" in _description(control).lower()
                )
                if is_seek_property and is_target:
                    violations.append(
                        (source_name, _description(control), info)
                    )
        self.assertEqual(violations, [])

    def test_native_progress_is_an_idle_fallback_not_custom_active(self):
        def assert_fallback_visibility(control: ET.Element):
            visible = re.sub(r"\s+", "", _visible_text(control))
            ready_empty = (
                "String.IsEmpty(Window(Home).Property(htpc.service.ready))"
            )
            active_empty = (
                "String.IsEmpty(Window(Home).Property(htpc.seek.viewactive))"
            )
            self.assertIn(ready_empty, visible)
            self.assertIn(active_empty, visible)
            alternatives = (
                re.escape(ready_empty)
                + r".*\|.*"
                + re.escape(active_empty)
                + "|"
                + re.escape(active_empty)
                + r".*\|.*"
                + re.escape(ready_empty)
            )
            self.assertRegex(visible, alternatives)

        seekbar_includes = [
            node
            for node in self.osd_root.findall("include")
            if node.get("name") == "SeekBar_Bingie"
        ]
        self.assertEqual(len(seekbar_includes), 1)
        native_progress = [
            control
            for control in seekbar_includes[0].iter("control")
            if control.findtext("info", default="").strip() == "Player.Progress"
            and "seek slider" in _description(control).lower()
        ]
        self.assertGreaterEqual(
            len(native_progress),
            1,
            "IncludesOSD.xml must retain a native Player.Progress fallback",
        )
        for control in native_progress:
            assert_fallback_visibility(control)

        panel_includes = [
            node
            for node in self.osd_root.findall("include")
            if node.get("name") == "OSDPanelBingie"
        ]
        self.assertEqual(len(panel_includes), 1)
        native_fills = [
            control
            for control in panel_includes[0].iter("control")
            if control.get("type") == "progress"
            and control.findtext("info", default="").strip()
            == "Player.Progress"
            and _description(control).lower() == "progress bar"
        ]
        self.assertEqual(
            len(native_fills),
            1,
            "BINGIE must retain one native red Player.Progress fill",
        )
        assert_fallback_visibility(native_fills[0])

        native_seek_markers = [
            control
            for control in panel_includes[0].iter("control")
            if control.get("type") == "slider"
            and control.get("id") == "401"
            and "Player.Seeking" in _visible_text(control)
        ]
        self.assertEqual(
            len(native_seek_markers),
            2,
            "both BINGIE native seek-marker variants must remain fallbacks",
        )
        for control in native_seek_markers:
            assert_fallback_visibility(control)

        native_timeline_ranges = [
            control
            for control in panel_includes[0].iter("control")
            if control.get("type") == "ranges"
            and control.findtext("info", default="").strip()
            in ("Player.Cutlist", "Player.Chapters")
        ]
        self.assertEqual(len(native_timeline_ranges), 2)
        for control in native_timeline_ranges:
            assert_fallback_visibility(control)

        native_epg_fills = [
            control
            for control in panel_includes[0].iter("control")
            if control.get("type") == "progress"
            and control.findtext("info", default="").strip()
            == "PVR.EpgEventProgress"
        ]
        self.assertEqual(len(native_epg_fills), 1)
        assert_fallback_visibility(native_epg_fills[0])

    def test_all_literal_playback_textures_are_packaged_or_known_upstream(self):
        compiled_textures = SKIN_ROOT / "media" / "Textures.xbt"
        if SKIN_ROOT == DEFAULT_SKIN_ROOT and not compiled_textures.is_file():
            assets_source = UPSTREAM_ASSETS.read_text(encoding="utf-8")
            self.assertRegex(
                assets_source,
                r'(?m)^\s*"media"\s*$',
                "source-mode tests require a pinned compiled-media declaration",
            )
        else:
            self.assertTrue(
                compiled_textures.is_file(),
                f"assembled skin is missing {compiled_textures}",
            )

        existing_xml = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(XML_ROOT.glob("*.xml"))
            if path != PLAYBACK_XML
        )
        missing = []
        for relative in sorted(set(_literal_texture_paths(self.playback_root))):
            candidate = SKIN_ROOT / "media" / relative
            if not candidate.is_file() and relative not in existing_xml:
                missing.append(relative)
        self.assertEqual(
            missing,
            [],
            "custom textures must be loose assets or pre-existing references "
            "in the pinned upstream skin (native runtime QA must still verify "
            "the compiled XBT index): "
            + ", ".join(missing),
        )

    def test_preview_anchors_match_the_deterministic_generator(self):
        for slot in anchors.SLOTS:
            with self.subTest(slot=slot):
                preview = self._one_slot_control(slot, PREVIEW_DESCRIPTION)
                rows = anchors.extract_anchor_rows(PLAYBACK_XML, slot)
                self.assertEqual(rows, anchors.anchor_rows())
                self.assertEqual(rows[0], (0, 0))
                self.assertEqual(rows[-1], (100, anchors.TIMELINE_WIDTH))
                self.assertEqual(preview.get("type"), "group")

    def test_no_diagnostic_controls_or_properties_ship(self):
        serialized = ET.tostring(
            self.playback_root, encoding="unicode"
        ).lower()
        forbidden = (
            "diagnostic",
            "htpc.render.diagnostics",
            "candidate a",
            "candidate b",
            "candidate c",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, serialized)


class ProducerSafetyContractTest(unittest.TestCase):
    def test_presenter_has_no_retained_control_mutation_path(self):
        source = PRESENTER.read_text(encoding="utf-8")
        self.assertNotIn(".getControl(", source)
        self.assertNotIn(".setPosition(", source)

    def test_presenter_publishes_every_renderer_property(self):
        source = PRESENTER.read_text(encoding="utf-8")
        contract = MEDIA_CONTRACT.read_text(encoding="utf-8")
        for property_name in (
            "viewslot",
            "targetfill",
            "targetmarker",
            "previewanchor",
        ):
            with self.subTest(property_name=property_name):
                self.assertIn(f'"{property_name}"', source)
                self.assertIn(
                    f'"{property_name}"',
                    contract,
                    "renderer properties must be cleared with SEEK_PROPERTY_KEYS",
                )


if __name__ == "__main__":
    unittest.main()
