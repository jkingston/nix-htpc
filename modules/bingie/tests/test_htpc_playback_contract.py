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
VIDEO_OSD_XML = XML_ROOT / "IncludesHTPCVideoOSD.xml"
INCLUDES_XML = XML_ROOT / "Includes.xml"
OSD_XML = XML_ROOT / "IncludesOSD.xml"
VIDEO_OSD_WINDOW_XML = XML_ROOT / "VideoOSD.xml"
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
LAYOUT_READY = "$PARAM[ready_condition]"
LAYOUT_VIEW_ACTIVE = (
    "Window($PARAM[property_window])."
    "Property($PARAM[property_prefix].viewactive)"
)
LAYOUT_VIEW_SLOT = (
    "Window($PARAM[property_window])."
    "Property($PARAM[property_prefix].viewslot)"
)


def _slot_info(slot: str, field: str) -> str:
    return (
        "Window($PARAM[property_window]).Property("
        f"$PARAM[property_prefix].{slot}.{field})"
    )


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
        "texturefocus",
        "texturenofocus",
        "alttexturefocus",
        "alttexturenofocus",
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
            for path in (
                SKIN_ROOT,
                PLAYBACK_XML,
                VIDEO_OSD_XML,
                INCLUDES_XML,
                OSD_XML,
                VIDEO_OSD_WINDOW_XML,
            )
            if not path.exists()
        ]
        self.assertEqual(
            missing,
            [],
            "the structural suite targets the planned vendored fork; "
            "missing: " + ", ".join(str(path) for path in missing),
        )


class ForkOwnedVideoOsdContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = (
            VIDEO_OSD_XML,
            INCLUDES_XML,
            VIDEO_OSD_WINDOW_XML,
        )
        if not all(path.is_file() for path in required):
            raise unittest.SkipTest("fork-owned video OSD source is absent")
        cls.root = ET.parse(VIDEO_OSD_XML).getroot()
        cls.playback_root = ET.parse(PLAYBACK_XML).getroot()
        cls.includes_root = ET.parse(INCLUDES_XML).getroot()
        cls.window_root = ET.parse(VIDEO_OSD_WINDOW_XML).getroot()
        definitions = [
            node
            for node in cls.root.findall("include")
            if node.get("name") == "HTPCVideoOSD"
        ]
        if len(definitions) != 1:
            raise AssertionError(
                "expected exactly one HTPCVideoOSD definition"
            )
        cls.surface = definitions[0]

    def _control(self, control_id: str) -> ET.Element:
        matches = [
            control
            for control in self.surface.iter("control")
            if control.get("id") == control_id
        ]
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one control id {control_id}",
        )
        return matches[0]

    @staticmethod
    def _actions(control: ET.Element, name: str) -> tuple[str, ...]:
        return tuple(
            (node.text or "").strip()
            for node in control.findall(name)
        )

    def test_surface_file_is_registered_once_but_not_live_yet(self):
        registrations = [
            node
            for node in self.includes_root.iter("include")
            if node.get("file") == VIDEO_OSD_XML.name
        ]
        self.assertEqual(len(registrations), 1)
        live_consumers = []
        for source in sorted(XML_ROOT.glob("*.xml")):
            if source == VIDEO_OSD_XML:
                continue
            serialized = source.read_text(
                encoding="utf-8-sig",
                errors="replace",
            )
            consumes_owned_osd = re.search(
                r'<include\b[^>]*\bcontent=["\']HTPCVideoOSD["\'][^>]*>'
                r"|<include\b[^>]*>\s*HTPCVideoOSD\s*</include>",
                serialized,
            )
            if consumes_owned_osd:
                live_consumers.append(source.name)
        self.assertEqual(
            live_consumers,
            [],
            "the structural OSD commit must not cut production over",
        )

    def test_fixture_sensitive_inputs_have_safe_production_defaults(self):
        parameters = {
            node.get("name"): node.get("default")
            for node in self.surface.findall("param")
        }
        self.assertEqual(parameters["visible"], "Player.HasVideo")
        self.assertEqual(parameters["buffer_progress"], "Player.ProgressCache")
        self.assertEqual(parameters["actual_progress"], "Player.Progress")
        self.assertEqual(
            parameters["chapter_available"],
            "!String.IsEmpty(Window(Home).Property(htpc.chapter.available))",
        )
        self.assertEqual(
            parameters["view_inactive_condition"],
            "String.IsEmpty(Window(Home).Property(htpc.seek.viewactive))",
        )
        self.assertEqual(
            parameters["presentation_ready"],
            "!String.IsEmpty(Window(Home).Property(htpc.service.ready))",
        )
        self.assertEqual(parameters["property_window"], "Home")
        self.assertEqual(parameters["property_prefix"], "htpc.seek")
        self.assertEqual(parameters["production_actions"], "true")
        self.assertEqual(parameters["inert_actions"], "false")

    def test_private_interactive_ids_are_unique_and_exclude_legacy_proxies(self):
        controls = [
            control
            for control in self.surface.iter("control")
            if control.get("id")
        ]
        ids = tuple(control.get("id") for control in controls)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(
            set(ids),
            {
                "9100",
                "9101",
                "9102",
                "9103",
                "9104",
                "9105",
                "9201",
                "9300",
            },
        )
        self.assertTrue(
            all(identifier.startswith(("91", "92", "93")) for identifier in ids)
        )
        self.assertTrue({"203", "187", "200", "300", "400", "401"}.isdisjoint(ids))

    def test_static_focus_graph_matches_the_owned_video_contract(self):
        transport = self._control("9201")
        self.assertEqual(self._actions(transport, "onleft"), ("noop",))
        self.assertEqual(self._actions(transport, "onright"), ("9300",))
        self.assertEqual(self._actions(transport, "onup"), ("9102",))

        timeline = self._control("9300")
        self.assertEqual(
            self._actions(timeline, "ondown")[-1],
            "9201",
        )
        self.assertIn(
            "NotifyAll(htpc.seek,timeline-left)",
            self._actions(timeline, "onleft"),
        )
        self.assertIn(
            "NotifyAll(htpc.seek,timeline-right)",
            self._actions(timeline, "onright"),
        )
        self.assertIn(
            "NotifyAll(htpc.seek,timeline-up)",
            self._actions(timeline, "onup"),
        )
        self.assertIn(
            "NotifyAll(htpc.seek,timeline-confirm)",
            self._actions(timeline, "onclick"),
        )

        top = self._control("9100")
        self.assertEqual(self._actions(top, "ondown"), ("9201",))
        self.assertEqual(self._actions(top, "onleft"), ("9100",))
        self.assertEqual(self._actions(top, "onright"), ("9100",))

    def test_every_numeric_navigation_target_resolves_locally(self):
        control_ids = {
            control.get("id")
            for control in self.surface.iter("control")
            if control.get("id")
        }
        unresolved = []
        for control in self.surface.iter("control"):
            for direction in ("onleft", "onright", "onup", "ondown"):
                for action in self._actions(control, direction):
                    if action.isdigit() and action not in control_ids:
                        unresolved.append(
                            (control.get("id"), direction, action)
                        )
        self.assertEqual(unresolved, [])

    def test_timeline_is_a_visible_focus_target_with_one_owned_presentation(self):
        timeline = self._control("9300")
        self.assertEqual(timeline.get("type"), "button")
        self.assertEqual(
            (timeline.findtext("texturefocus") or "").strip(),
            "colors/color_transparent.png",
        )
        playback_consumers = [
            node
            for node in self.surface.iter("include")
            if node.get("content") == "HTPCPlaybackPresentationLayout"
        ]
        self.assertEqual(len(playback_consumers), 1)
        focus_halo = _controls_by_description(
            self.surface,
            "HTPC video OSD timeline focus halo",
        )
        self.assertEqual(len(focus_halo), 1)
        self.assertEqual(focus_halo[0].findtext("height"), "11")
        self.assertEqual(
            _visible_text(focus_halo[0]),
            "Control.HasFocus(9300)",
        )
        self.assertFalse(
            any(
                control.get("type") == "slider"
                for control in self.surface.iter("control")
            ),
            "the owned surface must not add another slider authority",
        )

    def test_shared_rail_parameters_own_progress_geometry(self):
        progress_controls = [
            control
            for control in self.surface.iter("control")
            if control.get("type") == "progress"
        ]
        self.assertEqual(
            tuple(_description(control) for control in progress_controls),
            (
                "HTPC video OSD buffer",
                "HTPC video OSD actual progress",
            ),
        )
        for control in progress_controls:
            with self.subTest(description=_description(control)):
                self.assertEqual(control.findtext("left"), "$PARAM[rail_left]")
                self.assertEqual(control.findtext("top"), "$PARAM[rail_top]")
                self.assertEqual(
                    control.findtext("width"),
                    "$PARAM[rail_width]",
                )
                self.assertEqual(
                    control.findtext("height"),
                    "$PARAM[rail_height]",
                )
        actual = progress_controls[1]
        self.assertEqual(_visible_text(actual), "")

        playback_consumers = [
            node
            for node in self.surface.iter("include")
            if node.get("content") == "HTPCPlaybackPresentationLayout"
        ]
        self.assertEqual(len(playback_consumers), 1)
        passed_parameters = {
            node.get("name"): node.get("value")
            for node in playback_consumers[0].findall("param")
        }
        for parameter in (
            "rail_left",
            "rail_top",
            "rail_width",
            "rail_height",
            "marker_top",
            "marker_height",
            "target_marker_top",
            "target_marker_height",
            "preview_left",
            "preview_top",
            "preview_width",
            "preview_height",
        ):
            with self.subTest(parameter=parameter):
                self.assertEqual(
                    passed_parameters[parameter],
                    f"$PARAM[{parameter}]",
                )
        self.assertEqual(passed_parameters["target_fill_color"], "80ffffff")
        self.assertEqual(passed_parameters["stable_preview_card"], "true")
        self.assertEqual(
            passed_parameters["ready_condition"],
            "$PARAM[presentation_ready]",
        )
        self.assertEqual(
            passed_parameters["property_window"],
            "$PARAM[property_window]",
        )
        self.assertEqual(
            passed_parameters["property_prefix"],
            "$PARAM[property_prefix]",
        )

        for description in (
            TARGET_FILL_DESCRIPTION,
            CUT_MARKERS_DESCRIPTION,
            CHAPTER_MARKERS_DESCRIPTION,
            TARGET_MARKER_DESCRIPTION,
            PREVIEW_DESCRIPTION,
        ):
            controls = _controls_by_description(
                self.playback_root,
                description,
            )
            self.assertEqual(len(controls), 2)
            for control in controls:
                self.assertIn("$PARAM[", ET.tostring(
                    control,
                    encoding="unicode",
                ))

    def test_top_actions_are_video_only_and_bookmark_modal_is_absent(self):
        expected = {
            "9101": "PlayerControl(Stop)",
            "9102": "ActivateWindow(osdsubtitlesettings)",
            "9103": "ActivateWindow(osdaudiosettings)",
            "9104": "ActivateWindow(osdvideosettings)",
            "9105": "PlayerControl(ShowVideoMenu)",
        }
        for control_id, action in expected.items():
            with self.subTest(control_id=control_id):
                self.assertIn(
                    action,
                    self._actions(self._control(control_id), "onclick"),
                )
        serialized = ET.tostring(self.surface, encoding="unicode").lower()
        self.assertNotIn("videobookmarks", serialized)
        self.assertNotIn(">bookmarks<", serialized)
        self.assertNotIn("playercontrol(previous)", serialized)
        self.assertNotIn("playercontrol(next)", serialized)
        self.assertNotIn("stop_fo.png", serialized)
        self.assertNotIn("dvd_fo.png", serialized)

    def test_review_mode_can_disable_every_playback_side_effect(self):
        side_effects = (
            "ActivateWindow(",
            "NotifyAll(",
            "PlayerControl(",
            "SetFocus(",
            "StepBack",
            "StepForward",
        )
        ungated = []
        for control in self.surface.iter("control"):
            for action_name in (
                "onclick",
                "onfocus",
                "onunfocus",
                "onleft",
                "onright",
                "onup",
                "ondown",
            ):
                for action in control.findall(action_name):
                    value = (action.text or "").strip()
                    if not value.startswith(side_effects):
                        continue
                    if "$PARAM[production_actions]" not in action.get(
                        "condition",
                        "",
                    ):
                        ungated.append(
                            (control.get("id"), action_name, value)
                        )
        self.assertEqual(ungated, [])

        for control_id in (
            "9101",
            "9102",
            "9103",
            "9104",
            "9105",
            "9201",
            "9300",
        ):
            with self.subTest(control_id=control_id):
                control = self._control(control_id)
                inert_actions = [
                    (node.text or "").strip()
                    for node in control.findall("onclick")
                    if "$PARAM[inert_actions]"
                    in node.get("condition", "")
                ]
                self.assertEqual(inert_actions, ["noop"])

    def test_seek_preview_does_not_compete_with_metadata(self):
        for description in (
            "HTPC video OSD title",
            "HTPC video OSD subtitle",
        ):
            controls = _controls_by_description(self.surface, description)
            self.assertEqual(len(controls), 1)
            self.assertEqual(
                _visible_text(controls[0]),
                "$PARAM[view_inactive_condition]",
            )
        parameters = {
            node.get("name"): node.get("default")
            for node in self.surface.findall("param")
        }
        preview_time_bottom = int(parameters["preview_top"]) + 304
        self.assertLess(preview_time_bottom, int(parameters["time_top"]))
        transport_right = (
            int(parameters["transport_left"])
            + int(parameters["transport_size"])
        )
        self.assertLess(transport_right, int(parameters["elapsed_left"]))


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
            {node.get("name") for node in definitions},
            {
                "HTPCPlaybackPresentation",
                "HTPCPlaybackPresentationLayout",
            },
        )
        compatibility_wrapper = next(
            node
            for node in definitions
            if node.get("name") == "HTPCPlaybackPresentation"
        )
        wrapper_targets = [
            node.get("content")
            for node in compatibility_wrapper.findall(".//include")
        ]
        self.assertEqual(
            wrapper_targets,
            ["HTPCPlaybackPresentationLayout"],
        )
        parameterized_layout = next(
            node
            for node in definitions
            if node.get("name") == "HTPCPlaybackPresentationLayout"
        )
        layout_defaults = {
            node.get("name"): node.get("default")
            for node in parameterized_layout.findall("param")
        }
        self.assertEqual(
            layout_defaults["target_fill_color"],
            "$INFO[Skin.String(BingieOSDProgressBarColor)]",
        )
        self.assertEqual(layout_defaults["stable_preview_card"], "false")
        self.assertEqual(layout_defaults["preview_top"], "650")
        self.assertEqual(
            layout_defaults["ready_condition"],
            "!String.IsEmpty(Window(Home).Property(htpc.service.ready))",
        )
        self.assertEqual(layout_defaults["property_window"], "Home")
        self.assertEqual(layout_defaults["property_prefix"], "htpc.seek")
        consumers = [
            node
            for node in self.osd_root.iter("include")
            if (node.text or "").strip() == "HTPCPlaybackPresentation"
        ]
        self.assertEqual(
            len(consumers),
            1,
            "the inherited OSD must retain exactly one compatibility consumer",
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
                self.assertIn(LAYOUT_READY, visible)
                self.assertIn(
                    f"!String.IsEmpty({LAYOUT_VIEW_ACTIVE})",
                    visible,
                )
                self.assertIn(
                    f"String.IsEqual({LAYOUT_VIEW_SLOT},{slot})",
                    visible,
                )
                other = "b" if slot == "a" else "a"
                self.assertNotIn(
                    f"String.IsEqual({LAYOUT_VIEW_SLOT},{other})",
                    visible,
                )

    def test_layout_has_no_literal_binding_to_the_production_seek_namespace(self):
        layout = next(
            node
            for node in self.playback_root.findall("include")
            if node.get("name") == "HTPCPlaybackPresentationLayout"
        )
        definition = layout.find("definition")
        self.assertIsNotNone(definition)
        serialized = ET.tostring(definition, encoding="unicode")
        self.assertNotIn(
            "Window(Home).Property(htpc.seek.",
            serialized,
        )
        self.assertIn("$PARAM[property_window]", serialized)
        self.assertIn("$PARAM[property_prefix]", serialized)

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
            if path not in (PLAYBACK_XML, VIDEO_OSD_XML)
        )
        missing = []
        owned_textures = set(_literal_texture_paths(self.playback_root))
        video_osd_root = ET.parse(VIDEO_OSD_XML).getroot()
        owned_textures.update(_literal_texture_paths(video_osd_root))
        parameterized_icon_root = 'value="osd/bingie/"'
        for relative in sorted(owned_textures):
            candidate = SKIN_ROOT / "media" / relative
            known_parameterized_icon = (
                relative.startswith("osd/bingie/")
                and parameterized_icon_root in existing_xml
                and f"$PARAM[iconspath]{Path(relative).name}" in existing_xml
            )
            if (
                not candidate.is_file()
                and relative not in existing_xml
                and not known_parameterized_icon
            ):
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
                stable_card = self._one_slot_control(
                    slot,
                    "HTPC stable preview card",
                )
                self.assertIn(
                    "$PARAM[stable_preview_card]",
                    _visible_text(stable_card),
                )
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
