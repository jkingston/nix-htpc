from __future__ import annotations

import hashlib
import os
import re
import struct
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
VIDEO_OSD_REVIEW_XML = XML_ROOT / "Custom_1192_HTPCVideoOSDReview.xml"
INCLUDES_XML = XML_ROOT / "Includes.xml"
OSD_XML = XML_ROOT / "IncludesOSD.xml"
VIDEO_OSD_WINDOW_XML = XML_ROOT / "VideoOSD.xml"
PASSIVE_SEEK_HUD_XML = XML_ROOT / "DialogSeekBar.xml"
AUTO_CLOSE_OSD_XML = XML_ROOT / "Custom_1158_AutoCloseOSD.xml"
VIDEO_BOOKMARKS_XML = XML_ROOT / "VideoOSDBookmarks.xml"
BINGIE_SETTINGS_XML = SKIN_ROOT / "extras" / "bingiesettings.xml"
DEFAULT_SKIN_SETTINGS_XML = XML_ROOT / "IncludesDefaultSkinSettings.xml"
EN_GB_STRINGS = (
    SKIN_ROOT
    / "language"
    / "resource.language.en_gb"
    / "strings.po"
)
SETTINGS_ROOT = Path(
    os.environ.get(
        "HTPC_SETTINGS_ROOT",
        str(REPOSITORY_ROOT / "modules" / "kodi-settings-addon"),
    )
).resolve()
PRESENTER = SETTINGS_ROOT / "presenter.py"
MEDIA_CONTRACT = SETTINGS_ROOT / "media_contract.py"
UPSTREAM_ASSETS = BINGIE_ROOT / "upstream-assets.nix"
OSD_ASSETS = SKIN_ROOT / "resources" / "htpc" / "osd"
PINNED_UPSTREAM_XBT_TEXTURES = frozenset(
    {
        "osd/bingie/dvd.png",
        "osd/bingie/subtitles.png",
        "osd/bingie/subtitles_fo.png",
        "osd/bingie/video.png",
        "osd/bingie/video_fo.png",
    }
)

sys.path.insert(0, str(TOOLS_ROOT))

import generate_preview_anchors as anchors  # noqa: E402


TARGET_FILL_DESCRIPTION = "HTPC target progress fill"
TARGET_MARKER_DESCRIPTION = "HTPC target position marker"
CUT_MARKERS_DESCRIPTION = "HTPC cut markers"
CHAPTER_MARKERS_DESCRIPTION = "HTPC chapter markers"
PREVIEW_DESCRIPTION = anchors.PREVIEW_DESCRIPTION
PREVIEW_BACKPLATE_DESCRIPTION = "HTPC trick-play preview backplate"
PREVIEW_READY_DESCRIPTION = "HTPC trick-play ready state"
PREVIEW_LOADING_DESCRIPTION = "HTPC trick-play loading state"
PREVIEW_UNAVAILABLE_DESCRIPTION = "HTPC trick-play unavailable state"
LAYOUT_READY = "$PARAM[ready_condition]"
LAYOUT_VIEW_ACTIVE = (
    "Window($PARAM[property_window])."
    "Property($PARAM[property_prefix].viewactive)"
)
LAYOUT_VIEW_SLOT = (
    "Window($PARAM[property_window])."
    "Property($PARAM[property_prefix].viewslot)"
)
PRESENTATION_SLOTS = ("a", "b")
SLOT_PARAMETER = "$PARAM[slot]"
TIMELINE_MARKER_TEXTURE = (
    "special://skin/resources/htpc/osd/timeline-marker.png"
)


def _slot_info(field: str) -> str:
    return (
        "Window($PARAM[property_window]).Property("
        f"$PARAM[property_prefix].{SLOT_PARAMETER}.{field})"
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


def _png_contract(path: Path) -> tuple[int, int, int, int]:
    with path.open("rb") as source:
        header = source.read(26)
    if len(header) != 26 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    width, height = struct.unpack(">II", header[16:24])
    return width, height, header[24], header[25]


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
                VIDEO_OSD_REVIEW_XML,
                INCLUDES_XML,
                OSD_XML,
                VIDEO_OSD_WINDOW_XML,
                PASSIVE_SEEK_HUD_XML,
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
            PASSIVE_SEEK_HUD_XML,
        )
        if not all(path.is_file() for path in required):
            raise unittest.SkipTest("fork-owned video OSD source is absent")
        cls.root = ET.parse(VIDEO_OSD_XML).getroot()
        cls.playback_root = ET.parse(PLAYBACK_XML).getroot()
        cls.includes_root = ET.parse(INCLUDES_XML).getroot()
        cls.window_root = ET.parse(VIDEO_OSD_WINDOW_XML).getroot()
        cls.passive_hud_root = ET.parse(PASSIVE_SEEK_HUD_XML).getroot()
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

    def test_surface_is_registered_and_consumed_by_review_and_production(self):
        registrations = [
            node
            for node in self.includes_root.iter("include")
            if node.get("file") == VIDEO_OSD_XML.name
        ]
        self.assertEqual(len(registrations), 1)
        registered_files = [
            node.get("file")
            for node in self.includes_root.findall("include")
        ]
        self.assertLess(
            registered_files.index(PLAYBACK_XML.name),
            registered_files.index(VIDEO_OSD_XML.name),
            "the playback layout dependency must be registered before "
            "the owned video OSD",
        )
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
            [
                VIDEO_OSD_REVIEW_XML.name,
                VIDEO_OSD_WINDOW_XML.name,
            ],
            "the deterministic review host and production video OSD must "
            "consume the same owned surface",
        )

    def test_production_window_owns_surface_actions_and_properties(self):
        owned = [
            node
            for node in self.window_root.iter("include")
            if node.get("content") == "HTPCVideoOSD"
        ]
        self.assertEqual(len(owned), 1)
        parameters = {
            node.get("name"): node.get("value")
            for node in owned[0].findall("param")
        }
        self.assertEqual(
            parameters,
            {
                "visible": "Player.HasVideo",
                "presentation_ready": (
                    "!String.IsEmpty(Window(Home).Property("
                    "htpc.service.ready))"
                ),
                "modal_condition": (
                    "!String.IsEmpty(Window(Home).Property("
                    "htpc.service.ready)) + "
                    "!String.IsEmpty(Window(Home).Property("
                    "htpc.seek.modal))"
                ),
                "property_window": "Home",
                "property_prefix": "htpc.seek",
                "production_actions": "true",
                "inert_actions": "false",
                "preview_background_load": "true",
                "preview_visible_condition": (
                    "String.IsEmpty(Window(Home).Property("
                    "htpc.chapter.open))"
                ),
            },
        )
        inherited = [
            node
            for node in self.window_root.iter("include")
            if (node.text or "").strip() == "OSDButtonsModern"
        ]
        self.assertEqual(inherited, [])

    def test_production_window_focuses_transport_or_active_timeline(self):
        default_control = self.window_root.find("defaultcontrol")
        self.assertIsNotNone(default_control)
        self.assertEqual((default_control.text or "").strip(), "9201")
        self.assertEqual(default_control.get("always"), "true")
        focus_actions = [
            (
                node.get("condition"),
                (node.text or "").strip(),
            )
            for node in self.window_root.findall("onload")
            if (node.text or "").strip().startswith("SetFocus(")
        ]
        self.assertEqual(
            focus_actions,
            [
                (
                    "String.IsEmpty(Window(Home).Property("
                    "htpc.service.ready)) | "
                    "![Player.SeekEnabled + !VideoPlayer.Content(livetv) + "
                    "!VideoPlayer.HasMenu] | [String.IsEmpty(Window(Home).Property("
                    "htpc.seek.active)) + String.IsEmpty(Window(Home)."
                    "Property(htpc.seek.viewactive))]",
                    "SetFocus(9201)",
                ),
                (
                    "!String.IsEmpty(Window(Home).Property("
                    "htpc.service.ready)) + "
                    "[Player.SeekEnabled + !VideoPlayer.Content(livetv) + "
                    "!VideoPlayer.HasMenu] + [!String.IsEmpty(Window(Home).Property("
                    "htpc.seek.active)) | !String.IsEmpty(Window(Home)."
                    "Property(htpc.seek.viewactive))]",
                    "SetFocus(9300)",
                ),
            ],
        )

    def test_passive_seek_hud_is_globally_absent_behind_video_osd(self):
        visibility = tuple(
            (node.text or "").strip()
            for node in self.passive_hud_root.findall("visible")
        )
        self.assertEqual(
            visibility,
            (
                "Window.IsActive(fullscreenvideo)",
                "!Window.IsVisible(VideoOSD.xml)",
                (
                    "Player.ShowInfo | Player.Seeking | "
                    "Player.DisplayAfterSeek | "
                    "!String.IsEmpty(Player.SeekNumeric) | "
                    "[Player.Paused + !Player.Caching] | "
                    "Player.Forwarding | Player.Rewinding"
                ),
            ),
            "DialogSeekBar must be a passive fullscreen HUD whose whole "
            "window disappears while VideoOSD remains visible, including "
            "beneath child dialogs",
        )
        nested_guards = [
            node
            for control in self.passive_hud_root.iter("control")
            for node in control.findall("visible")
            if (node.text or "").strip()
            == "!Window.IsVisible(VideoOSD.xml)"
        ]
        self.assertEqual(
            nested_guards,
            [],
            "the OSD exclusion must guard the window, not selected children",
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
            parameters["seekable_condition"],
            "Player.SeekEnabled + !VideoPlayer.Content(livetv) + "
            "!VideoPlayer.HasMenu",
        )
        self.assertEqual(
            parameters["timeline_chapter_hint_label"],
            "$LOCALIZE[31581]",
        )
        self.assertEqual(
            parameters["chapter_available_condition"],
            "!String.IsEmpty(Window(Home).Property("
            "htpc.chapter.available))",
        )
        self.assertEqual(
            parameters["preview_loading_label"],
            "$LOCALIZE[31582]",
        )
        self.assertEqual(
            parameters["preview_unavailable_label"],
            "$LOCALIZE[31583]",
        )
        self.assertEqual(
            parameters["view_inactive_condition"],
            "[String.IsEmpty(Window(Home).Property(htpc.service.ready)) | "
            "String.IsEmpty(Window(Home).Property(htpc.seek.viewactive))]",
        )
        self.assertEqual(
            parameters["presentation_ready"],
            "!String.IsEmpty(Window(Home).Property(htpc.service.ready))",
        )
        self.assertEqual(parameters["modal_condition"], "false")
        self.assertEqual(parameters["property_window"], "Home")
        self.assertEqual(parameters["property_prefix"], "htpc.seek")
        self.assertEqual(parameters["production_actions"], "true")
        self.assertEqual(parameters["inert_actions"], "false")
        self.assertEqual(parameters["preview_background_load"], "true")
        self.assertEqual(parameters["preview_visible_condition"], "true")
        self.assertNotIn(
            "htpc.chapter.open",
            ET.tostring(self.root, encoding="unicode"),
        )

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
        self.assertIn("9300", self._actions(transport, "onright"))
        self.assertIn("9102", self._actions(transport, "onup"))

        timeline = self._control("9300")
        self.assertIn("9201", self._actions(timeline, "ondown"))
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

    def test_transition_barrier_has_exact_ready_dead_and_review_branches(self):
        ready = (
            "!String.IsEmpty(Window(Home).Property(htpc.service.ready))"
        )
        dead = "String.IsEmpty(Window(Home).Property(htpc.service.ready))"
        production = "$PARAM[production_actions]"
        inert = "$PARAM[inert_actions]"
        seekable = "$PARAM[seekable_condition]"
        not_seekable = "![$PARAM[seekable_condition]]"
        modal = "$PARAM[modal_condition]"
        not_modal = "![$PARAM[modal_condition]]"

        def branches(control_id, action_name):
            return tuple(
                (
                    action.get("condition"),
                    (action.text or "").strip(),
                )
                for action in self._control(control_id).findall(action_name)
            )

        self.assertEqual(
            branches("9201", "onup"),
            (
                (
                    f"{production} + {ready}",
                    "NotifyAll(htpc.seek,transport-up)",
                ),
                (f"{production} + {dead}", "9102"),
                (inert, "9102"),
            ),
        )
        self.assertEqual(
            branches("9201", "onright"),
            (
                (
                    f"{production} + {seekable} + {ready}",
                    "NotifyAll(htpc.seek,transport-right)",
                ),
                (
                    f"{production} + {seekable} + {dead}",
                    "9300",
                ),
                (f"{production} + {not_seekable}", "noop"),
                (f"{inert} + {seekable}", "9300"),
                (f"{inert} + {not_seekable}", "noop"),
            ),
        )
        self.assertEqual(
            branches("9201", "ondown"),
            (
                (
                    f"{production} + {ready}",
                    "NotifyAll(htpc.seek,transport-down)",
                ),
                (f"{production} + {dead}", "noop"),
                (inert, "noop"),
            ),
        )
        self.assertEqual(
            branches("9300", "onup"),
            (
                (
                    f"{production} + {modal}",
                    "noop",
                ),
                (
                    f"{production} + {not_modal} + {ready}",
                    "NotifyAll(htpc.seek,timeline-up)",
                ),
                (
                    f"{production} + {not_modal} + {dead}",
                    "9102",
                ),
                (inert, "9102"),
            ),
        )
        self.assertEqual(
            branches("9300", "ondown"),
            (
                (
                    f"{production} + {ready}",
                    "NotifyAll(htpc.seek,timeline-down)",
                ),
                (
                    f"{production} + {dead} + {modal}",
                    "noop",
                ),
                (
                    f"{production} + {dead} + {not_modal}",
                    "9201",
                ),
                (inert, "9201"),
            ),
        )

        serialized = ET.tostring(self.surface, encoding="unicode")
        self.assertNotIn("Action(Down", serialized)
        self.assertNotIn(
            "Window(Home).Property(htpc.seek.modal)",
            serialized,
            "the reusable OSD must consume its modal parameter rather than "
            "couple navigation to production property names",
        )

    def test_modal_scrub_hides_only_unreachable_normal_chrome(self):
        not_modal = "![$PARAM[modal_condition]]"
        expected_visibility = {
            "HTPC video OSD top gradient": not_modal,
            "HTPC video OSD top actions": not_modal,
            "HTPC video OSD focused top action label": not_modal,
            "HTPC video OSD title": (
                "$PARAM[view_inactive_condition] + " + not_modal
            ),
            "HTPC video OSD subtitle": (
                "$PARAM[view_inactive_condition] + " + not_modal
            ),
            "HTPC play pause": not_modal,
            "HTPC video OSD elapsed time": not_modal,
            "HTPC video OSD remaining time": not_modal,
            "HTPC video OSD chapter hint": (
                "$PARAM[presentation_ready] + $PARAM[seekable_condition] + "
                "Control.HasFocus(9300) + "
                "$PARAM[chapter_available_condition] + "
                "$PARAM[view_inactive_condition] + " + not_modal
            ),
        }
        for description, visibility in expected_visibility.items():
            with self.subTest(description=description):
                controls = _controls_by_description(
                    self.surface,
                    description,
                )
                self.assertEqual(len(controls), 1)
                control = controls[0]
                self.assertEqual(_visible_text(control), visibility)
                animations = control.findall("animation")
                self.assertEqual(
                    tuple((node.text or "").strip() for node in animations),
                    ("Visible", "Hidden"),
                )
                for animation in animations:
                    self.assertEqual(animation.get("effect"), "fade")
                    self.assertEqual(animation.get("time"), "120")
                    self.assertIsNone(animation.get("condition"))

        bottom_gradient = _controls_by_description(
            self.surface,
            "HTPC video OSD bottom gradient",
        )
        self.assertEqual(len(bottom_gradient), 1)
        self.assertEqual(_visible_text(bottom_gradient[0]), "")

        timeline = self._control("9300")
        self.assertEqual(
            _visible_text(timeline),
            "$PARAM[seekable_condition]",
        )
        presentation = [
            node
            for node in self.surface.iter("include")
            if node.get("content") == "HTPCPlaybackPresentationSlot"
        ]
        self.assertEqual(len(presentation), 2)
        for consumer in presentation:
            self.assertNotIn(
                "modal_condition",
                {
                    node.get("name")
                    for node in consumer.findall("param")
                },
                "modal mode must retain the target presentation unchanged",
            )

    def test_non_seekable_media_keeps_an_informational_unfocused_rail(self):
        timeline = self._control("9300")
        self.assertEqual(
            tuple(
                (visible.text or "").strip()
                for visible in timeline.findall("visible")
            ),
            ("$PARAM[seekable_condition]",),
        )

        chapter_hint = _controls_by_description(
            self.surface,
            "HTPC video OSD chapter hint",
        )[0]
        self.assertEqual(
            _visible_text(chapter_hint),
            "$PARAM[presentation_ready] + $PARAM[seekable_condition] + "
            "Control.HasFocus(9300) + "
            "$PARAM[chapter_available_condition] + "
            "$PARAM[view_inactive_condition] + "
            "![$PARAM[modal_condition]]",
        )

        progress_controls = [
            control
            for control in self.surface.iter("control")
            if control.get("type") == "progress"
        ]
        base_progress = [
            control
            for control in progress_controls
            if "focused" not in _description(control)
        ]
        self.assertEqual(len(base_progress), 2)
        focused_visibility = (
            "$PARAM[seekable_condition] + Control.HasFocus(9300) + "
            "$PARAM[view_inactive_condition]"
        )
        for control in base_progress:
            with self.subTest(description=_description(control)):
                self.assertEqual(
                    _visible_text(control),
                    f"![{focused_visibility}]",
                )
        focused_rail = _controls_by_description(
            self.surface,
            "HTPC video OSD focused timeline rail",
        )
        self.assertEqual(len(focused_rail), 1)
        self.assertEqual(
            _visible_text(focused_rail[0]),
            focused_visibility,
        )
        forbidden_actions = {
            "animation",
            "onclick",
            "ondown",
            "onfocus",
            "onleft",
            "onright",
            "onunfocus",
            "onup",
        }
        for node in focused_rail[0].iter():
            with self.subTest(tag=node.tag):
                self.assertIsNone(node.get("id"))
                self.assertNotIn(node.tag, forbidden_actions)

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

    def test_timeline_is_a_visible_focus_target_with_two_owned_slots(self):
        timeline = self._control("9300")
        self.assertEqual(timeline.get("type"), "button")
        self.assertEqual(
            (timeline.findtext("texturefocus") or "").strip(),
            "colors/color_transparent.png",
        )
        playback_consumers = [
            node
            for node in self.surface.iter("include")
            if node.get("content") == "HTPCPlaybackPresentationSlot"
        ]
        self.assertEqual(len(playback_consumers), 2)
        self.assertEqual(
            tuple(
                consumer.find("param[@name='slot']").get("value")
                for consumer in playback_consumers
            ),
            PRESENTATION_SLOTS,
        )
        chapter_hints = _controls_by_description(
            self.surface,
            "HTPC video OSD chapter hint",
        )
        self.assertEqual(len(chapter_hints), 1)
        self.assertEqual(
            _visible_text(chapter_hints[0]),
            "$PARAM[presentation_ready] + $PARAM[seekable_condition] + "
            "Control.HasFocus(9300) + "
            "$PARAM[chapter_available_condition] + "
            "$PARAM[view_inactive_condition] + "
            "![$PARAM[modal_condition]]",
        )
        self.assertEqual(
            _controls_by_description(
                self.surface,
                "HTPC video OSD timeline focus halo",
            ),
            [],
        )
        self.assertFalse(
            any(
                control.get("type") == "slider"
                for control in self.surface.iter("control")
            ),
            "the owned surface must not add another slider authority",
        )

    def test_focused_actual_playhead_reuses_target_ranges_geometry(self):
        focused_groups = _controls_by_description(
            self.surface,
            "HTPC video OSD focused timeline rail",
        )
        self.assertEqual(len(focused_groups), 1)
        focused_group = focused_groups[0]
        self.assertEqual(
            _visible_text(focused_group),
            "$PARAM[seekable_condition] + Control.HasFocus(9300) + "
            "$PARAM[view_inactive_condition]",
        )

        actual_markers = [
            control
            for control in focused_group.findall("control")
            if _description(control)
            == "HTPC video OSD focused actual playhead"
        ]
        self.assertEqual(len(actual_markers), 1)
        actual_marker = actual_markers[0]
        self.assertEqual(actual_marker.get("type"), "ranges")
        self.assertIsNone(actual_marker.get("id"))
        self.assertEqual(
            actual_marker.findtext("info", default="").strip(),
            "Window($PARAM[property_window]).Property("
            "$PARAM[property_prefix].actualmarker)",
        )
        self.assertEqual(
            _visible_text(actual_marker),
            "$PARAM[presentation_ready] + !String.IsEmpty(Window("
            "$PARAM[property_window]).Property("
            "$PARAM[property_prefix].actualmarker))",
        )

        geometry_and_textures = (
            "posx",
            "posy",
            "width",
            "height",
            "texturebg",
            "lefttexture",
            "midtexture",
            "righttexture",
        )

        def node_contract(control: ET.Element, tag: str):
            node = control.find(tag)
            self.assertIsNotNone(node, tag)
            return (
                (node.text or "").strip(),
                tuple(sorted(node.attrib.items())),
            )

        target_markers = _controls_by_description(
            self.playback_root,
            TARGET_MARKER_DESCRIPTION,
        )
        self.assertEqual(len(target_markers), 1)
        for target_marker in target_markers:
            with self.subTest(
                target_info=target_marker.findtext("info", default="")
            ):
                self.assertEqual(target_marker.get("type"), "ranges")
                for tag in geometry_and_textures:
                    self.assertEqual(
                        node_contract(actual_marker, tag),
                        node_contract(target_marker, tag),
                        tag,
                    )

        forbidden_actions = {
            "onclick",
            "ondown",
            "onfocus",
            "onleft",
            "onright",
            "onunfocus",
            "onup",
        }
        self.assertTrue(
            forbidden_actions.isdisjoint(
                node.tag for node in actual_marker.iter()
            )
        )

    def test_chapter_hint_is_compact_noninteractive_and_localized(self):
        hint = _controls_by_description(
            self.surface,
            "HTPC video OSD chapter hint",
        )[0]
        parameters = {
            node.get("name"): node.get("default")
            for node in self.surface.findall("param")
        }
        expected_geometry = {
            "left": "timeline_chapter_hint_left",
            "top": "timeline_chapter_hint_top",
            "width": "timeline_chapter_hint_width",
            "height": "timeline_chapter_hint_height",
        }
        for node, parameter in expected_geometry.items():
            with self.subTest(node=node):
                self.assertEqual(
                    hint.findtext(node),
                    f"$PARAM[{parameter}]",
                )

        self.assertEqual(hint.get("type"), "label")
        self.assertEqual(hint.findtext("align"), "center")
        self.assertEqual(hint.findtext("aligny"), "center")
        self.assertEqual(hint.findtext("textcolor"), "b3ffffff")
        self.assertEqual(
            hint.findtext("label"),
            "$PARAM[timeline_chapter_hint_label]",
        )
        self.assertEqual(
            _visible_text(hint),
            "$PARAM[presentation_ready] + $PARAM[seekable_condition] + "
            "Control.HasFocus(9300) + "
            "$PARAM[chapter_available_condition] + "
            "$PARAM[view_inactive_condition] + "
            "![$PARAM[modal_condition]]",
        )

        forbidden_tags = {
            "info",
            "onclick",
            "ondown",
            "onleft",
            "onright",
            "onup",
        }
        for node in hint.iter():
            with self.subTest(tag=node.tag):
                self.assertIsNone(node.get("id"))
                self.assertNotIn(node.tag, forbidden_tags)
                self.assertNotIn(
                    node.get("type"),
                    {"progress", "ranges", "slider"},
                )
        serialized = ET.tostring(hint, encoding="unicode")
        for token in ("Player.", "htpc.seek", "NotifyAll(", "SetFocus("):
            with self.subTest(token=token):
                self.assertNotIn(token, serialized)

        rail_left = int(parameters["rail_left"])
        rail_right = rail_left + int(parameters["rail_width"])
        hint_left = int(parameters["timeline_chapter_hint_left"])
        hint_right = hint_left + int(parameters["timeline_chapter_hint_width"])
        hint_top = int(parameters["timeline_chapter_hint_top"])
        hint_bottom = hint_top + int(parameters["timeline_chapter_hint_height"])
        self.assertGreaterEqual(hint_left, rail_left)
        self.assertLessEqual(hint_right, rail_right)
        self.assertEqual(
            (2 * hint_left) + int(parameters["timeline_chapter_hint_width"]),
            rail_left + rail_right,
        )
        self.assertLess(hint_bottom, int(parameters["timeline_focus_rail_top"]))

        self.assertEqual(
            parameters["timeline_chapter_hint_label"],
            "$LOCALIZE[31581]",
        )
        self.assertEqual(
            parameters["chapter_available_condition"],
            "!String.IsEmpty(Window(Home).Property("
            "htpc.chapter.available))",
        )
        strings = EN_GB_STRINGS.read_text(encoding="utf-8")
        chapter_match = re.search(
            r'msgctxt "#31581"\s+msgid "([^"]+)"',
            strings,
        )
        self.assertIsNotNone(chapter_match)
        self.assertEqual(chapter_match.group(1), "↑  Chapters")
        self.assertNotIn('msgctxt "#31580"', strings)

        focus_visuals = [
            _description(control)
            for control in self.surface.iter("control")
            if "Control.HasFocus(9300)" in _visible_text(control)
            and not _visible_text(control).startswith("![")
        ]
        self.assertEqual(
            focus_visuals,
            [
                "HTPC video OSD focused timeline rail",
                "HTPC video OSD chapter hint",
            ],
        )
        self.assertEqual(
            [
                node
                for node in self.root.findall("variable")
                if node.get("name") == "HTPCVideoOSDTimelineFocusCueLabel"
            ],
            [],
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
                "HTPC video OSD focused buffer",
                "HTPC video OSD focused actual progress",
            ),
        )
        for control in progress_controls:
            with self.subTest(description=_description(control)):
                self.assertEqual(control.findtext("left"), "$PARAM[rail_left]")
                self.assertEqual(
                    control.findtext("width"),
                    "$PARAM[rail_width]",
                )
                self.assertEqual(
                    control.findtext("reveal"),
                    "true",
                    "progress textures must reveal their numeric percentage "
                    "instead of retaining the source texture width",
                )
        buffer, actual, focused_buffer, focused_actual = progress_controls
        for control in (buffer, actual):
            with self.subTest(description=_description(control)):
                self.assertEqual(control.findtext("top"), "$PARAM[rail_top]")
                self.assertEqual(
                    control.findtext("height"),
                    "$PARAM[rail_height]",
                )
        for control in (focused_buffer, focused_actual):
            with self.subTest(description=_description(control)):
                self.assertEqual(
                    control.findtext("top"),
                    "$PARAM[timeline_focus_rail_top]",
                )
                self.assertEqual(
                    control.findtext("height"),
                    "$PARAM[timeline_focus_rail_height]",
                )
        for control in (buffer, focused_buffer):
            self.assertEqual(
                control.findtext("info"),
                "$PARAM[buffer_progress]",
            )
        for control in (actual, focused_actual):
            self.assertEqual(
                control.findtext("info"),
                "$PARAM[actual_progress]",
            )
        focused_visibility = (
            "$PARAM[seekable_condition] + Control.HasFocus(9300) + "
            "$PARAM[view_inactive_condition]"
        )
        for control in (buffer, actual):
            self.assertEqual(
                _visible_text(control),
                f"![{focused_visibility}]",
            )

        parameters = {
            node.get("name"): node.get("default")
            for node in self.surface.findall("param")
        }
        normal_center = (
            int(parameters["rail_top"])
            + int(parameters["rail_height"]) / 2
        )
        focus_center = (
            int(parameters["timeline_focus_rail_top"])
            + int(parameters["timeline_focus_rail_height"]) / 2
        )
        target_center = (
            int(parameters["target_marker_top"])
            + int(parameters["target_marker_height"]) / 2
        )
        self.assertEqual(focus_center, normal_center)
        self.assertEqual(target_center, normal_center)
        self.assertEqual(
            parameters["target_marker_top"],
            parameters["timeline_focus_rail_top"],
        )
        self.assertEqual(
            parameters["target_marker_height"],
            parameters["timeline_focus_rail_height"],
        )
        self.assertGreater(
            int(parameters["timeline_focus_rail_height"]),
            int(parameters["rail_height"]),
        )

        playback_consumers = [
            node
            for node in self.surface.iter("include")
            if node.get("content") == "HTPCPlaybackPresentationSlot"
        ]
        self.assertEqual(len(playback_consumers), 2)
        parameter_maps = [
            {
                node.get("name"): node.get("value")
                for node in consumer.findall("param")
            }
            for consumer in playback_consumers
        ]
        self.assertEqual(
            tuple(parameters.pop("slot") for parameters in parameter_maps),
            PRESENTATION_SLOTS,
        )
        self.assertEqual(parameter_maps[0], parameter_maps[1])
        passed_parameters = parameter_maps[0]
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
        self.assertNotIn("stable_preview_card", passed_parameters)
        self.assertEqual(
            passed_parameters["preview_loading_label"],
            "$PARAM[preview_loading_label]",
        )
        self.assertEqual(
            passed_parameters["preview_unavailable_label"],
            "$PARAM[preview_unavailable_label]",
        )
        self.assertEqual(
            passed_parameters["preview_background_load"],
            "$PARAM[preview_background_load]",
        )
        self.assertEqual(
            passed_parameters["preview_visible_condition"],
            "$PARAM[preview_visible_condition]",
        )
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
            self.assertEqual(len(controls), 1)
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
                "$PARAM[view_inactive_condition] + "
                "![$PARAM[modal_condition]]",
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
        required = (PLAYBACK_XML, VIDEO_OSD_XML, INCLUDES_XML, OSD_XML)
        if not all(path.is_file() for path in required):
            raise unittest.SkipTest(
                "planned modules/bingie/src fork is not present yet"
            )
        cls.playback_root = ET.parse(PLAYBACK_XML).getroot()
        video_osd_root = ET.parse(VIDEO_OSD_XML).getroot()
        cls.video_osd_surface = next(
            node
            for node in video_osd_root.findall("include")
            if node.get("name") == "HTPCVideoOSD"
        )
        cls.includes_root = ET.parse(INCLUDES_XML).getroot()
        cls.osd_root = ET.parse(OSD_XML).getroot()

    def _slot_group(self) -> ET.Element:
        matches = _controls_by_description(
            self.playback_root,
            anchors.SLOT_DESCRIPTION,
        )
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one {anchors.SLOT_DESCRIPTION!r} control",
        )
        return matches[0]

    def _one_slot_control(self, description: str) -> ET.Element:
        matches = _controls_by_description(self._slot_group(), description)
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one {description!r} control",
        )
        return matches[0]

    def test_local_playback_slot_has_two_direct_owned_surface_consumers(self):
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
            {"HTPCPlaybackPresentationSlot"},
        )
        slot_template = definitions[0]
        slot_defaults = {
            node.get("name"): node.get("default")
            for node in slot_template.findall("param")
        }
        self.assertEqual(
            slot_defaults,
            {
                "slot": None,
                "visible": "Player.HasVideo",
                "rail_left": "384",
                "rail_top": "964",
                "rail_width": "1152",
                "rail_height": "7",
                "marker_top": "955",
                "marker_height": "24",
                "target_marker_top": "962",
                "target_marker_height": "11",
                "preview_left": "194",
                "preview_top": "650",
                "preview_width": "380",
                "preview_height": "320",
                "target_fill_color": (
                    "$INFO[Skin.String(BingieOSDProgressBarColor)]"
                ),
                "preview_loading_label": "$LOCALIZE[31582]",
                "preview_unavailable_label": "$LOCALIZE[31583]",
                "preview_background_load": "true",
                "preview_visible_condition": "true",
                "ready_condition": (
                    "!String.IsEmpty(Window(Home).Property("
                    "htpc.service.ready))"
                ),
                "property_window": "Home",
                "property_prefix": "htpc.seek",
            },
            "the slot template must preserve every former layout default",
        )
        self.assertNotIn("stable_preview_card", slot_defaults)
        osd_defaults = {
            node.get("name"): node.get("default")
            for node in self.video_osd_surface.findall("param")
        }
        for parameter in ("target_marker_top", "target_marker_height"):
            with self.subTest(parameter=parameter):
                self.assertEqual(
                    slot_defaults[parameter],
                    osd_defaults[parameter],
                )

        slot_consumers = []
        modern_consumers = []
        for source in sorted(XML_ROOT.glob("*.xml")):
            serialized = source.read_text(
                encoding="utf-8-sig",
                errors="replace",
            )
            for target, consumers in (
                ("HTPCPlaybackPresentationSlot", slot_consumers),
                ("OSDButtonsModern", modern_consumers),
            ):
                pattern = (
                    r'<include\b[^>]*\bcontent=["\']'
                    + re.escape(target)
                    + r'["\'][^>]*>'
                    + r"|<include\b(?![^>]*\bname=)[^>]*>\s*"
                    + re.escape(target)
                    + r"\s*</include>"
                )
                consumers.extend(
                    source.name
                    for _match in re.finditer(pattern, serialized)
                )

        self.assertEqual(
            slot_consumers,
            [VIDEO_OSD_XML.name, VIDEO_OSD_XML.name],
            "only the owned video OSD may instantiate the two slots",
        )
        all_xml = "\n".join(
            source.read_text(encoding="utf-8-sig", errors="replace")
            for source in sorted(XML_ROOT.glob("*.xml"))
        )
        self.assertNotIn("HTPCPlaybackPresentationLayout", all_xml)
        self.assertEqual(
            modern_consumers,
            ["MusicOSD.xml"],
            "the inherited OSD factory is retained only for MusicOSD",
        )

        consumers = [
            node
            for node in self.video_osd_surface.iter("include")
            if node.get("content") == "HTPCPlaybackPresentationSlot"
        ]
        self.assertEqual(len(consumers), 2)
        parameter_maps = [
            {
                parameter.get("name"): parameter.get("value")
                for parameter in consumer.findall("param")
            }
            for consumer in consumers
        ]
        self.assertEqual(
            tuple(parameters.pop("slot") for parameters in parameter_maps),
            PRESENTATION_SLOTS,
            "slot instances must retain deterministic A then B order",
        )
        self.assertEqual(parameter_maps[0], parameter_maps[1])
        self.assertEqual(
            set(parameter_maps[0]),
            set(slot_defaults) - {"slot"},
            "both direct instances must forward the complete former map",
        )

    def test_slot_template_specializes_to_two_mutually_exclusive_namespaces(self):
        visible = _visible_text(self._slot_group())
        self.assertEqual(
            visible,
            f"{LAYOUT_READY} + !String.IsEmpty({LAYOUT_VIEW_ACTIVE}) + "
            f"String.IsEqual({LAYOUT_VIEW_SLOT},{SLOT_PARAMETER}) + "
            "$PARAM[visible]",
        )
        definition = next(
            node
            for node in self.playback_root.findall("include")
            if node.get("name") == "HTPCPlaybackPresentationSlot"
        ).find("definition")
        self.assertIsNotNone(definition)
        serialized = ET.tostring(definition, encoding="unicode")
        self.assertNotIn(
            "Window(Home).Property(htpc.seek.",
            serialized,
        )
        self.assertNotIn("htpc.chapter.open", serialized)
        self.assertIn("$PARAM[property_window]", serialized)
        self.assertIn("$PARAM[property_prefix]", serialized)
        self.assertNotIn(".a.", serialized)
        self.assertNotIn(".b.", serialized)
        for slot in PRESENTATION_SLOTS:
            with self.subTest(slot=slot):
                specialized = serialized.replace(SLOT_PARAMETER, slot)
                self.assertIn(
                    f"String.IsEqual({LAYOUT_VIEW_SLOT},{slot})",
                    specialized,
                )
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
                    self.assertIn(f".{slot}.{field}", specialized)

    def test_target_fill_and_marker_are_ranges_controls(self):
        contracts = {
            TARGET_FILL_DESCRIPTION: "targetfill",
            TARGET_MARKER_DESCRIPTION: "targetmarker",
        }
        for description, field in contracts.items():
            with self.subTest(description=description):
                control = self._one_slot_control(description)
                self.assertEqual(control.get("type"), "ranges")
                self.assertEqual(
                    control.findtext("info", default="").strip(),
                    _slot_info(field),
                )

    def test_target_marker_geometry_and_texture_are_canonical(self):
        marker = self._one_slot_control(TARGET_MARKER_DESCRIPTION)
        self.assertEqual(
            marker.findtext("posy"),
            "$PARAM[target_marker_top]",
        )
        self.assertEqual(
            marker.findtext("height"),
            "$PARAM[target_marker_height]",
        )
        self.assertEqual(
            marker.findtext("texturebg"),
            TIMELINE_MARKER_TEXTURE,
        )
        self.assertEqual(
            marker.findtext("lefttexture"),
            TIMELINE_MARKER_TEXTURE,
        )

    def test_timeline_marker_asset_is_square_and_matches_control_height(self):
        slot_template = next(
            node
            for node in self.playback_root.findall("include")
            if node.get("name") == "HTPCPlaybackPresentationSlot"
        )
        defaults = {
            node.get("name"): node.get("default")
            for node in slot_template.findall("param")
        }
        diameter = int(defaults["target_marker_height"])
        marker_png = OSD_ASSETS / "timeline-marker.png"
        marker_svg = OSD_ASSETS / "timeline-marker.svg"

        self.assertEqual(diameter, 11)
        self.assertEqual(
            _png_contract(marker_png),
            (diameter, diameter, 8, 6),
            "the runtime marker must be an 8-bit square RGBA texture whose "
            "intrinsic width equals the ranges control height",
        )
        self.assertEqual(
            hashlib.sha256(marker_png.read_bytes()).hexdigest(),
            "cb02f3c388eead254f6822e9da6cdbf7"
            "6e4dd36c7bfa9896738f39f3867ca471",
        )

        namespace = {"svg": "http://www.w3.org/2000/svg"}
        source = ET.parse(marker_svg).getroot()
        self.assertEqual(source.get("width"), str(diameter))
        self.assertEqual(source.get("height"), str(diameter))
        self.assertEqual(source.get("viewBox"), "0 0 11 11")
        disc = source.find("svg:circle", namespace)
        self.assertIsNotNone(disc)
        self.assertEqual(
            {
                attribute: disc.get(attribute)
                for attribute in ("cx", "cy", "r", "fill")
            },
            {
                "cx": "5.5",
                "cy": "5.5",
                "r": "5.25",
                "fill": "#ffffff",
            },
        )

    def test_marker_layers_preserve_exact_native_order(self):
        expected = (
            TARGET_FILL_DESCRIPTION,
            CUT_MARKERS_DESCRIPTION,
            CHAPTER_MARKERS_DESCRIPTION,
            TARGET_MARKER_DESCRIPTION,
        )
        descriptions = tuple(
            _description(control)
            for control in self._slot_group().findall("control")
            if _description(control) in expected
        )
        self.assertEqual(descriptions, expected)
        cut = self._one_slot_control(CUT_MARKERS_DESCRIPTION)
        chapter = self._one_slot_control(CHAPTER_MARKERS_DESCRIPTION)
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

    def test_chapter_availability_hint_is_owned_by_video_osd(self):
        seekbars = [
            node
            for node in self.osd_root.findall("include")
            if node.get("name") == "SeekBar_Bingie"
        ]
        self.assertEqual(len(seekbars), 1)
        self.assertNotIn(
            "htpc.chapter.available",
            ET.tostring(seekbars[0], encoding="unicode"),
        )
        hints = _controls_by_description(
            self.video_osd_surface,
            "HTPC video OSD chapter hint",
        )
        self.assertEqual(len(hints), 1)
        parameters = {
            node.get("name"): node.get("default")
            for node in self.video_osd_surface.findall("param")
        }
        self.assertEqual(
            parameters["chapter_available_condition"],
            "!String.IsEmpty(Window(Home).Property("
            "htpc.chapter.available))",
        )
        self.assertIn(
            "$PARAM[chapter_available_condition]",
            _visible_text(hints[0]),
        )

    def test_inherited_music_osd_seekbar_uses_native_navigation(self):
        factories = [
            node
            for node in self.osd_root.findall("include")
            if node.get("name") == "OSDButtonsModern"
        ]
        self.assertEqual(len(factories), 1)
        self.assertEqual(
            tuple(
                (
                    (node.text or "").strip(),
                    node.get("condition"),
                )
                for node in factories[0].findall("include")
                if (node.text or "").strip()
                in ("SeekBar_Bingie", "SeekBar_Clasic")
            ),
            (
                (
                    "SeekBar_Bingie",
                    "Skin.HasSetting(UseBingieOSD) + "
                    "[Player.HasVideo | Player.HasAudio]",
                ),
                (
                    "SeekBar_Clasic",
                    "!Skin.HasSetting(UseBingieOSD) + "
                    "[Player.HasVideo | Player.HasAudio]",
                ),
            ),
        )
        seekbars = [
            node
            for node in self.osd_root.findall("include")
            if node.get("name") == "SeekBar_Bingie"
        ]
        self.assertEqual(len(seekbars), 1)
        buttons = [
            control
            for control in seekbars[0].findall("control")
            if control.get("type") == "button"
            and control.get("id") == "187"
        ]
        self.assertEqual(len(buttons), 1)
        button = buttons[0]
        self.assertEqual(
            tuple(
                (
                    node.tag,
                    (node.text or "").strip(),
                    tuple(sorted(node.attrib.items())),
                )
                for node in button
            ),
            (
                ("include", "HiddenObject", ()),
                ("onup", "300", ()),
                ("ondown", "200", ()),
                ("onright", "StepForward", ()),
                ("onleft", "StepBack", ()),
                ("onclick", "PlayerControl(Play)", ()),
            ),
        )
        sliders = [
            control
            for control in seekbars[0].findall("control")
            if control.get("type") == "slider"
        ]
        self.assertEqual(len(sliders), 1)
        self.assertEqual(
            sliders[0].findtext("info", default="").strip(),
            "Player.Progress",
        )
        self.assertEqual(
            _visible_text(sliders[0]),
            "Control.HasFocus(187) + [Player.HasVideo | Player.HasAudio]",
        )

    def test_inherited_music_osd_control_inventories(self):
        layouts = {
            node.get("name"): node
            for node in self.osd_root.findall("include")
            if node.get("name")
            in ("OSDButtons_Layout", "OSDButtons_Bingie_Layout")
        }
        self.assertEqual(
            set(layouts),
            {"OSDButtons_Layout", "OSDButtons_Bingie_Layout"},
        )

        def direct_control_ids(layout_name: str, group_id: str):
            groups = [
                control
                for control in layouts[layout_name].iter("control")
                if control.get("id") == group_id
            ]
            self.assertEqual(len(groups), 1)
            return tuple(
                control.get("id")
                for control in groups[0].findall("control")
                if control.get("id")
            )

        self.assertEqual(
            direct_control_ids("OSDButtons_Layout", "200"),
            (
                "201",
                "202",
                "203",
                "204",
                "205",
                "206",
                "207",
                "208",
                "210",
                "212",
                "10",
                "101",
                "105",
                "701",
                "500",
                "21417",
                "703",
                "806",
                "807",
                "811",
                "808",
            ),
        )
        self.assertEqual(
            direct_control_ids("OSDButtons_Bingie_Layout", "400"),
            (
                "204",
                "10",
                "101",
                "811",
                "500",
                "701",
                "806",
                "807",
                "808",
            ),
        )
        serialized_layouts = " ".join(
            ET.tostring(layout, encoding="unicode")
            for layout in layouts.values()
        ).lower()
        for retired_action in (
            "ActivateWindow(VideoBookmarks)",
            "ActivateWindow(osdsubtitlesettings)",
            "ActivateWindow(123)",
            "PlayerControl(ShowVideoMenu)",
            "StereoMode",
            "seek(-298800)",
        ):
            with self.subTest(retired_action=retired_action):
                self.assertNotIn(retired_action.lower(), serialized_layouts)

        descriptions = [
            node
            for node in self.osd_root.findall("variable")
            if node.get("name") == "osd_button_description"
        ]
        self.assertEqual(len(descriptions), 1)
        focus_pattern = re.compile(r"Control\.HasFocus\(([0-9]+)\)")
        focus_ids = []
        for node in descriptions[0].findall("value"):
            match = focus_pattern.fullmatch(node.get("condition", ""))
            if match:
                focus_ids.append(match.group(1))
        self.assertEqual(
            tuple(focus_ids),
            (
                "10",
                "101",
                "204",
                "500",
                "701",
                "806",
                "807",
                "808",
                "811",
            ),
        )

    def test_inherited_music_osd_settings_match_defaults(self):
        settings_root = ET.parse(BINGIE_SETTINGS_XML).getroot()
        option_groups = [
            setting
            for setting in settings_root.iter("setting")
            if setting.get("id") == "bingie_osd_buttons"
        ]
        self.assertEqual(len(option_groups), 1)
        option_ids = tuple(
            option.get("id")
            for option in option_groups[0].findall("option")
        )
        self.assertEqual(
            option_ids,
            (
                "bingie_osd_buttons_back",
                "bingie_osd_buttons_record",
                "bingie_osd_buttons_audio",
                "bingie_osd_buttons_channellist",
                "bingie_osd_buttons_pvrguide",
                "bingie_osd_buttons_playlist",
                "bingie_osd_buttons_viz",
                "bingie_osd_buttons_lyrics",
                "bingie_osd_buttons_info",
            ),
        )
        defaults_root = ET.parse(DEFAULT_SKIN_SETTINGS_XML).getroot()
        default_ids = []
        default_pattern = re.compile(
            r"Skin\.SetBool\((bingie_osd_buttons_[^)]+)\)"
        )
        for node in defaults_root.iter("onload"):
            match = default_pattern.fullmatch((node.text or "").strip())
            if match:
                default_ids.append(match.group(1))
        self.assertEqual(len(default_ids), len(set(default_ids)))
        self.assertEqual(set(default_ids), set(option_ids))
        bingie_layouts = [
            node
            for node in self.osd_root.findall("include")
            if node.get("name") == "OSDButtons_Bingie_Layout"
        ]
        self.assertEqual(len(bingie_layouts), 1)
        consumer_ids = set(
            re.findall(
                r"Skin\.HasSetting\((bingie_osd_buttons_[^)]+)\)",
                ET.tostring(bingie_layouts[0], encoding="unicode"),
            )
        )
        self.assertEqual(consumer_ids, set(option_ids))

    def test_conventional_video_bookmark_window_is_retained(self):
        bookmark_root = ET.parse(VIDEO_BOOKMARKS_XML).getroot()
        self.assertEqual(
            bookmark_root.findtext("defaultcontrol", default="").strip(),
            "1",
        )
        core_controls = tuple(
            (
                control.get("id"),
                control.get("type"),
                _description(control),
            )
            for control in bookmark_root.iter("control")
            if control.get("id") in {"2", "3", "4", "11"}
        )
        self.assertEqual(
            core_controls,
            (
                ("11", "list", ""),
                ("2", "button", "Add"),
                ("3", "button", "Delete"),
                ("4", "button", "Episode Bookmarks"),
            ),
        )
        passive_seek = PASSIVE_SEEK_HUD_XML.read_text(encoding="utf-8-sig")
        auto_close = AUTO_CLOSE_OSD_XML.read_text(encoding="utf-8-sig")
        self.assertIn("Window.IsActive(videobookmarks)", passive_seek)
        self.assertIn("Window.IsVisible(VideoOSDBookmarks.xml)", passive_seek)
        self.assertIn("Window.IsActive(videobookmarks)", auto_close)

    def test_native_video_progress_is_idle_fallback_not_custom_active(self):
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
                and relative not in PINNED_UPSTREAM_XBT_TEXTURES
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
        preview = self._one_slot_control(PREVIEW_DESCRIPTION)
        rows = anchors.extract_anchor_rows(PLAYBACK_XML)
        self.assertEqual(rows, anchors.anchor_rows())
        self.assertEqual(rows[0], (0, 0))
        self.assertEqual(rows[-1], (100, anchors.TIMELINE_WIDTH))
        self.assertEqual(preview.get("type"), "group")
        conditions = tuple(
            animation.get("condition", "")
            for animation in preview.findall("animation")
        )
        self.assertEqual(len(conditions), 101)
        for slot in PRESENTATION_SLOTS:
            with self.subTest(slot=slot):
                specialized = tuple(
                    condition.replace(SLOT_PARAMETER, slot)
                    for condition in conditions
                )
                self.assertTrue(
                    all(f".{slot}.previewanchor" in value for value in specialized)
                )

    def test_preview_card_states_are_explicit_and_canonical(self):
        forbidden_tags = {
            "onclick",
            "ondown",
            "onfocus",
            "onleft",
            "onright",
            "onunfocus",
            "onup",
        }
        preview = self._one_slot_control(PREVIEW_DESCRIPTION)
        status = _slot_info("previewstatus")
        path = _slot_info("previewpath")
        self.assertEqual(
            _visible_text(preview),
            "$PARAM[preview_visible_condition] + "
            f"!String.IsEmpty({_slot_info('targetvalid')})",
        )
        self.assertEqual(
            [
                _description(control)
                for control in self.playback_root.iter("control")
                if "$PARAM[preview_visible_condition]"
                in _visible_text(control)
            ],
            [PREVIEW_DESCRIPTION],
        )
        expected_visibility = {
            PREVIEW_READY_DESCRIPTION: (
                f"String.IsEqual({status},ready) + "
                f"!String.IsEmpty({path})"
            ),
            PREVIEW_LOADING_DESCRIPTION: (
                f"String.IsEqual({status},loading)"
            ),
            PREVIEW_UNAVAILABLE_DESCRIPTION: (
                f"!String.IsEqual({status},none) + "
                f"!String.IsEqual({status},loading) + "
                f"[!String.IsEqual({status},ready) | "
                f"String.IsEmpty({path})]"
            ),
        }

        backplate = self._one_slot_control(
            PREVIEW_BACKPLATE_DESCRIPTION,
        )
        self.assertEqual(backplate.get("type"), "image")
        self.assertEqual(_visible_text(backplate), "")
        self.assertEqual(backplate.findtext("posx"), "-10")
        self.assertEqual(backplate.findtext("posy"), "-10")
        self.assertEqual(backplate.findtext("width"), "400")
        self.assertEqual(backplate.findtext("height"), "234")
        texture = backplate.find("texture")
        self.assertEqual(
            (texture.text or "").strip(),
            "diffuse/panel2.png",
        )
        self.assertEqual(texture.get("border"), "12")
        self.assertEqual(texture.get("colordiffuse"), "ee080808")

        ready = self._one_slot_control(PREVIEW_READY_DESCRIPTION)
        loading = self._one_slot_control(PREVIEW_LOADING_DESCRIPTION)
        unavailable = self._one_slot_control(
            PREVIEW_UNAVAILABLE_DESCRIPTION,
        )
        states = (ready, loading, unavailable)
        self.assertEqual(
            tuple(control.get("type") for control in states),
            ("group", "label", "label"),
        )
        for control in states:
            with self.subTest(state=_description(control)):
                self.assertEqual(
                    _visible_text(control),
                    expected_visibility[_description(control)],
                )
                for node in control.iter():
                    self.assertIsNone(node.get("id"))
                    self.assertNotIn(node.tag, forbidden_tags)

        ready_images = [
            control
            for control in ready.findall("control")
            if control.get("type") == "image"
        ]
        self.assertEqual(len(ready_images), 1)
        ready_image = ready_images[0]
        self.assertEqual(ready_image.findtext("width"), "380")
        self.assertEqual(ready_image.findtext("height"), "214")
        ready_texture = ready_image.find("texture")
        self.assertEqual(
            (ready_texture.text or "").strip(),
            f"$INFO[{path}]",
        )
        self.assertEqual(
            ready_texture.get("background"),
            "$PARAM[preview_background_load]",
        )
        path_textures = [
            texture
            for texture in preview.iter("texture")
            if path in (texture.text or "")
        ]
        self.assertEqual(path_textures, [ready_texture])

        labels = {
            loading: "$PARAM[preview_loading_label]",
            unavailable: "$PARAM[preview_unavailable_label]",
        }
        for control, label in labels.items():
            self.assertEqual(control.findtext("width"), "380")
            self.assertEqual(control.findtext("height"), "214")
            self.assertEqual(control.findtext("font"), "Reg22")
            self.assertEqual(control.findtext("align"), "center")
            self.assertEqual(control.findtext("aligny"), "center")
            self.assertEqual(
                control.findtext("textcolor"),
                "b3ffffff",
            )
            self.assertEqual(control.findtext("label"), label)

        self.assertNotIn(
            "stable_preview_card",
            ET.tostring(self.playback_root, encoding="unicode"),
        )
        strings = EN_GB_STRINGS.read_text(encoding="utf-8")
        localized = {}
        for string_id in ("31582", "31583"):
            match = re.search(
                rf'msgctxt "#{string_id}"\s+msgid "([^"]+)"',
                strings,
            )
            self.assertIsNotNone(match)
            localized[string_id] = match.group(1)
        self.assertEqual(localized["31582"], "Loading preview…")
        self.assertEqual(localized["31583"], "Preview unavailable")

        review_root = ET.parse(VIDEO_OSD_REVIEW_XML).getroot()
        review_include = next(
            node
            for node in review_root.iter("include")
            if node.get("content") == "HTPCVideoOSD"
        )
        review_parameters = {
            node.get("name"): node.get("value")
            for node in review_include.findall("param")
        }
        self.assertEqual(
            review_parameters["preview_loading_label"],
            localized["31582"],
        )
        self.assertEqual(
            review_parameters["preview_unavailable_label"],
            localized["31583"],
        )
        self.assertNotIn("preview_visible_condition", review_parameters)

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
            "actualmarker",
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
                    "renderer properties must be in CURRENT_SEEK_PROPERTY_KEYS",
                )

    def test_production_skin_does_not_consume_retired_seek_properties(self):
        production_sources = (
            PLAYBACK_XML,
            VIDEO_OSD_XML,
            VIDEO_OSD_WINDOW_XML,
            OSD_XML,
            PASSIVE_SEEK_HUD_XML,
            AUTO_CLOSE_OSD_XML,
        )
        source = "\n".join(
            path.read_text(encoding="utf-8-sig", errors="strict")
            for path in production_sources
        )
        forbidden = (
            r"htpc\.seek\.(?:a|b)\.(?:revision|phase)\b",
            r"htpc\.seek\.preview(?:ready|path)\b",
            r"\$PARAM\[property_prefix\]\.\$PARAM\[slot\]\."
            r"(?:revision|phase)\b",
        )
        for pattern in forbidden:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, source))

    def test_presenter_targets_only_owned_video_osd_focus_ids(self):
        source = PRESENTER.read_text(encoding="utf-8")
        for legacy in ("SetFocus(300)", "SetFocus(187)", "SetFocus(203)"):
            with self.subTest(legacy=legacy):
                self.assertNotIn(legacy, source)
        self.assertIn("TOP_BAR_CONTROL_ID = 9102", source)
        self.assertIn("TIMELINE_CONTROL_ID = 9300", source)
        self.assertIn("TRANSPORT_CONTROL_ID = 9201", source)


if __name__ == "__main__":
    unittest.main()
