from __future__ import annotations

import json
import os
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


BINGIE_ROOT = Path(__file__).resolve().parents[1]
SKIN_ROOT = Path(
    os.environ.get("BINGIE_SKIN_ROOT", str(BINGIE_ROOT / "src"))
).resolve()
XML_ROOT = SKIN_ROOT / "1080i"
SHORTCUTS_ROOT = SKIN_ROOT / "shortcuts"
CONTRACT_PATH = Path(__file__).parent / "fixtures" / "home_contract.json"
MAX_CONTRACT_BYTES = 64 * 1024

HOME_XML = XML_ROOT / "Home.xml"
INCLUDES_XML = XML_ROOT / "Includes.xml"
BINGIE_XML = XML_ROOT / "IncludesBingie.xml"
HOME_BINGIE_XML = XML_ROOT / "IncludesHomeBingie.xml"
PATHS_XML = XML_ROOT / "IncludesPaths.xml"
SKIN_SETTINGS_XML = XML_ROOT / "SkinSettings.xml"
ADDON_XML = SKIN_ROOT / "addon.xml"
MAIN_MENU_XML = SHORTCUTS_ROOT / "mainmenu.DATA.xml"
HOME_ROWS_XML = SHORTCUTS_ROOT / "10000-1.DATA.xml"

OBSERVATION_KEYS = {
    "evidence",
    "settings",
    "controls",
    "home_entry_trace",
    "sidebar",
    "interaction_traces",
    "rows",
}
SOURCE_KEYS = {
    "menu_provider",
    "home_rows_provider",
    "home_bootstrap_control",
    "sidebar_right_source_action",
    "generated_movies_tv_submenus",
    "skin_shortcuts_build_groups",
    "checked_in_default_mainmenu",
}
INTENT_KEYS = {"controls", "navigation", "home", "library_hubs"}
CONTROL_IDS = {"spotlight": 1508, "sidebar": 900, "widget_group": 77777}
SIDEBAR_LABELS = [
    "Search",
    "Home",
    "Anime",
    "Movies",
    "TV Shows",
    "Settings",
    "Power",
]
ROW_KEYS = [
    "continue_watching_movies",
    "next_anime_episodes",
    "next_tv_episodes",
    "recently_added_movies",
    "recently_added_anime",
    "recently_added_tv_shows",
]
ROW_RECORD_KEYS = {
    "key",
    "id",
    "label",
    "art",
    "limit",
    "normalized_locator",
}
LOCATOR_KEYS = {"provider", "operation", "media_type", "target", "role"}
INTENT_ROW_KEYS = {
    "key",
    "label",
    "art",
    "limit",
    "provider",
    "operation",
    "media_type",
    "target",
    "role",
    "empty_behavior",
}
DEFAULT_WIDGET_VARIABLES = [
    ("$VAR[DefWidgetName]", "$VAR[DefWidgetContent]"),
    ("$VAR[DefWidget1Name]", "$VAR[DefWidget1Content]"),
    ("$VAR[DefWidget2Name]", "$VAR[DefWidget2Content]"),
    ("$VAR[DefWidget3Name]", "$VAR[DefWidget3Content]"),
    ("$VAR[DefWidget4Name]", "$VAR[DefWidget4Content]"),
    ("$VAR[DefWidget5Name]", "$VAR[DefWidget5Content]"),
]
SKIN_SHORTCUTS_BUILD_GROUPS = [
    "mainmenu",
    "powermenu",
    "searchmenu",
    "tmdbsearchmenu",
    "moviehub",
    "tvshowhub",
    "newhub",
    "musichub",
    "customhub",
    "somethinghub",
    "mylisthub",
]
ALLOWED_PUBLIC_URIS = {
    "videodb://movies/genres/",
    "videodb://tvshows/genres/",
}
PRIVATE_KEY_FRAGMENTS = {
    "apikey",
    "authtoken",
    "collectionid",
    "hostname",
    "parentid",
    "password",
    "profilename",
    "serverid",
    "token",
    "username",
}


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value):
    raise ValueError(f"non-finite JSON number: {value}")


def _loads_contract(text: str) -> dict:
    value = json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite,
    )
    if not isinstance(value, dict):
        raise ValueError("Home contract must be a JSON object")
    return value


def _load_contract() -> dict:
    if CONTRACT_PATH.stat().st_size > MAX_CONTRACT_BYTES:
        raise ValueError("Home contract exceeds the 64 KiB review limit")
    return _loads_contract(CONTRACT_PATH.read_text(encoding="utf-8"))


def _assert_no_private_state(value, path=()):
    """Reject machine-specific capture data while allowing public Kodi paths."""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
            if any(part in normalized_key for part in PRIVATE_KEY_FRAGMENTS):
                raise AssertionError(
                    f"private field at {'.'.join(path + (key,))}"
                )
            _assert_no_private_state(child, path + (key,))
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_private_state(child, path + (str(index),))
        return
    if not isinstance(value, str) or value in ALLOWED_PUBLIC_URIS:
        return

    dotted = ".".join(path)
    if dotted == "deployed_observation.evidence.skin_version":
        return
    if "://" in value:
        raise AssertionError(f"raw URI at {dotted}")
    if re.search(r"[/\\](?:home|Users|root)[/\\]", value):
        raise AssertionError(f"personal path at {dotted}")
    if re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        value,
        re.IGNORECASE,
    ):
        raise AssertionError(f"UUID at {dotted}")
    if re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", value):
        raise AssertionError(f"IPv4 address at {dotted}")


def _parse(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def _text(node: ET.Element | None) -> str:
    return "" if node is None else (node.text or "").strip()


def _definition(root: ET.Element, name: str) -> ET.Element:
    matches = [
        node for node in root.findall("include") if node.get("name") == name
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one direct include definition {name!r}, "
            f"found {len(matches)}"
        )
    return matches[0]


def _control(root: ET.Element, control_id: int) -> ET.Element:
    matches = [
        node
        for node in root.iter("control")
        if node.get("id") == str(control_id)
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one control id {control_id}, found {len(matches)}"
        )
    return matches[0]


def _actions(node: ET.Element, name: str) -> list[ET.Element]:
    return list(node.findall(name))


def _action_texts(node: ET.Element, name: str) -> list[str]:
    return [_text(action) for action in _actions(node, name)]


class HomeManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = _load_contract()
        cls.observed = cls.contract["deployed_observation"]
        cls.source = cls.contract["transitional_source_snapshot"]
        cls.intended = cls.contract["intended_declarative_contract"]

    def test_sections_are_closed_and_separate_observation_source_and_intent(self):
        self.assertEqual(
            set(self.contract),
            {
                "schema_version",
                "deployed_observation",
                "transitional_source_snapshot",
                "intended_declarative_contract",
                "current_state_to_intent_deltas",
            },
        )
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(set(self.observed), OBSERVATION_KEYS)
        self.assertEqual(set(self.source), SOURCE_KEYS)
        self.assertEqual(set(self.intended), INTENT_KEYS)
        self.assertEqual(
            set(self.observed["evidence"]),
            {"repository_revision", "skin_version", "generated_include"},
        )
        self.assertEqual(
            set(self.observed["settings"]),
            {"explicit", "absent_effective_values"},
        )
        self.assertEqual(
            self.observed["settings"]["explicit"],
            {
                "MovieDetailsHome": True,
                "EnableFixedFrameWidgets": True,
                "widgetstyle": "poster",
                "WidgetsGlobalLimit": 15,
            },
        )
        self.assertEqual(
            self.observed["settings"]["absent_effective_values"],
            {
                "DisableAllSubmenus": False,
                "AutoShowSubmenu": False,
                "DisableSpotlightContent": False,
            },
        )

    def test_snapshot_identity_and_runtime_observations_are_well_formed(self):
        evidence = self.observed["evidence"]
        self.assertRegex(evidence["repository_revision"], r"\A[0-9a-f]{40}\Z")
        self.assertRegex(evidence["skin_version"], r"\A\d+(?:\.\d+){3}\Z")
        generated = evidence["generated_include"]
        self.assertEqual(
            set(generated),
            {"name", "sha256", "lines", "bytes"},
        )
        self.assertEqual(
            generated["name"],
            "script-skinshortcuts-includes.xml",
        )
        self.assertRegex(generated["sha256"], r"\A[0-9a-f]{64}\Z")
        self.assertGreater(generated["lines"], 0)
        self.assertGreater(generated["bytes"], 0)

        self.assertEqual(self.observed["controls"], CONTROL_IDS)
        self.assertEqual(
            self.observed["home_entry_trace"],
            {
                "window": 10000,
                "focus": "spotlight_first_item",
                "spotlight_info": "empty",
                "item_ready": True,
            },
        )
        traces = self.observed["interaction_traces"]["sidebar_right"]
        self.assertEqual([trace["item"] for trace in traces], ["Movies", "TV Shows"])
        for trace in traces:
            self.assertEqual(
                set(trace),
                {
                    "item",
                    "start_window",
                    "start_control",
                    "result_window",
                    "result_focus_group",
                    "submenu_visible",
                },
            )
            self.assertEqual(trace["start_window"], 10000)
            self.assertEqual(trace["start_control"], CONTROL_IDS["sidebar"])
            self.assertEqual(trace["result_window"], 10000)
            self.assertEqual(
                trace["result_focus_group"],
                CONTROL_IDS["widget_group"],
            )
            self.assertFalse(trace["submenu_visible"])

    def test_observation_is_sanitized_and_uses_closed_semantic_records(self):
        _assert_no_private_state(self.contract)
        sidebar = self.observed["sidebar"]
        self.assertEqual([item["label"] for item in sidebar], SIDEBAR_LABELS)
        for item in sidebar:
            self.assertEqual(set(item), {"label", "destination"})
            self.assertEqual(set(item["destination"]), {"kind", "target"})
            self.assertIsInstance(item["label"], str)
            self.assertIn(
                item["destination"]["kind"],
                {"custom_window", "builtin_window", "library_role"},
            )
            self.assertIn(
                type(item["destination"]["target"]),
                {int, str},
            )

        rows = self.observed["rows"]
        self.assertEqual([row["key"] for row in rows], ROW_KEYS)
        self.assertEqual(len({row["id"] for row in rows}), len(rows))
        for row in rows:
            with self.subTest(row=row["key"]):
                self.assertEqual(set(row), ROW_RECORD_KEYS)
                self.assertRegex(row["key"], r"\A[a-z][a-z0-9_]*\Z")
                self.assertIs(type(row["id"]), int)
                self.assertGreater(row["id"], 0)
                self.assertIsInstance(row["label"], str)
                self.assertIn(row["art"], {"landscape", "poster"})
                self.assertEqual(row["limit"], 15)
                locator = row["normalized_locator"]
                self.assertEqual(set(locator), LOCATOR_KEYS)
                self.assertEqual(locator["provider"], "jellyfin")
                self.assertEqual(locator["operation"], "browse")
                self.assertIn(locator["media_type"], {"movie", "episode"})
                self.assertIn(
                    locator["target"],
                    {"inprogress", "nextepisodes", "recentlyadded"},
                )
                self.assertIn(locator["role"], {"movies", "anime", "tvshows"})
                for value in locator.values():
                    self.assertRegex(value, r"\A[a-z][a-z0-9_]*\Z")

    def test_source_snapshot_and_library_hubs_use_closed_records(self):
        self.assertEqual(
            self.source["skin_shortcuts_build_groups"],
            SKIN_SHORTCUTS_BUILD_GROUPS,
        )
        self.assertIs(type(self.source["home_bootstrap_control"]), int)
        self.assertIs(
            type(self.source["generated_movies_tv_submenus"]),
            bool,
        )
        defaults = self.source["checked_in_default_mainmenu"]
        self.assertEqual(len(defaults), 7)
        for record in defaults:
            self.assertEqual(
                set(record),
                {"label", "default_id", "action"},
            )
            self.assertIsInstance(record["label"], str)
            self.assertIn(type(record["default_id"]), {str, type(None)})
            self.assertRegex(
                record["action"],
                r"\AActivateWindow\((?:[0-9]+|home),return\)\Z",
            )

        hubs = self.intended["library_hubs"]
        self.assertEqual(set(hubs), {"movies", "tvshows"})
        for role, hub in hubs.items():
            with self.subTest(role=role):
                self.assertEqual(set(hub), {"window", "genre_row"})
                self.assertIs(type(hub["window"]), int)
                self.assertEqual(set(hub["genre_row"]), {"id", "path"})
                self.assertIs(type(hub["genre_row"]["id"]), int)
                self.assertIn(
                    hub["genre_row"]["path"],
                    ALLOWED_PUBLIC_URIS,
                )

    def test_intent_preserves_semantics_without_generated_row_ids(self):
        self.assertEqual(self.intended["controls"], self.observed["controls"])
        navigation = self.intended["navigation"]
        self.assertEqual(
            set(navigation),
            {
                "provider",
                "sidebar_right_behavior",
                "focus_restoration",
                "submenu_policy",
                "sidebar",
            },
        )
        self.assertEqual(navigation["provider"], "fork_owned")
        self.assertEqual(
            navigation["sidebar_right_behavior"],
            {
                "inactive_destination": "activate_selected_destination",
                "active_destination": (
                    "close_sidebar_and_restore_content_focus"
                ),
            },
        )
        self.assertEqual(
            navigation["focus_restoration"],
            {
                "sidebar_selection": "preserve_selected_entry",
                "destination_content": "preserve_last_focused_item",
            },
        )
        observed_sidebar = {
            item["label"]: item["destination"]
            for item in self.observed["sidebar"]
        }
        intended_sidebar = navigation["sidebar"]
        self.assertEqual(
            [item["label"] for item in intended_sidebar],
            SIDEBAR_LABELS,
        )
        for item in intended_sidebar:
            self.assertEqual(set(item), {"label", "destination"})
            self.assertEqual(set(item["destination"]), {"kind", "target"})
        intended_destinations = {
            item["label"]: item["destination"] for item in intended_sidebar
        }
        for label in {"Search", "Home", "Anime", "Settings", "Power"}:
            self.assertEqual(
                intended_destinations[label],
                observed_sidebar[label],
            )
        self.assertEqual(
            intended_destinations["Movies"],
            {"kind": "custom_window", "target": 1111},
        )
        self.assertEqual(
            intended_destinations["TV Shows"],
            {"kind": "custom_window", "target": 1110},
        )
        self.assertEqual(
            navigation["submenu_policy"],
            {"Movies": "disabled", "TV Shows": "disabled"},
        )

        home = self.intended["home"]
        self.assertEqual(
            set(home),
            {"rows_provider", "bootstrap_control", "default_focus", "rows"},
        )
        self.assertEqual(home["rows_provider"], "fork_owned")
        self.assertIs(type(home["bootstrap_control"]), int)
        observed_semantics = []
        for row in self.observed["rows"]:
            locator = row["normalized_locator"]
            observed_semantics.append(
                {
                    "key": row["key"],
                    "label": row["label"],
                    "art": row["art"],
                    "limit": row["limit"],
                    "provider": locator["provider"],
                    "operation": locator["operation"],
                    "media_type": locator["media_type"],
                    "target": locator["target"],
                    "role": locator["role"],
                }
            )
        intended_rows = home["rows"]
        for row in intended_rows:
            self.assertEqual(set(row), INTENT_ROW_KEYS)
            self.assertEqual(row["empty_behavior"], "omit_row")
        self.assertEqual(
            [
                {
                    key: value
                    for key, value in row.items()
                    if key != "empty_behavior"
                }
                for row in intended_rows
            ],
            observed_semantics,
        )
        self.assertEqual(
            home["default_focus"],
            {
                "control": CONTROL_IDS["spotlight"],
                "primary_state": "play",
                "activation_by_media_type": {
                    "playable_video": "play_resume",
                    "tvshow": "open_show",
                    "music_container": "open_music",
                },
            },
        )

    def test_every_current_to_intent_difference_has_a_typed_basis(self):
        delta_records = self.contract["current_state_to_intent_deltas"]
        deltas = {
            item["area"]: item
            for item in delta_records
        }
        self.assertEqual(len(delta_records), len(deltas))
        self.assertEqual(
            set(deltas),
            {
                "sidebar_right_inactive_destination",
                "movies_tv_destination",
                "menu_provider",
                "home_rows_provider",
                "generated_movies_tv_submenus",
            },
        )
        self.assertEqual(
            deltas["sidebar_right_inactive_destination"]["current"],
            self.source["sidebar_right_source_action"],
        )
        self.assertEqual(
            deltas["sidebar_right_inactive_destination"]["intended"],
            self.intended["navigation"]["sidebar_right_behavior"][
                "inactive_destination"
            ],
        )

        observed_destinations = {
            item["label"]: item["destination"]
            for item in self.observed["sidebar"]
        }
        intended_destinations = {
            item["label"]: item["destination"]
            for item in self.intended["navigation"]["sidebar"]
        }
        self.assertTrue(
            all(
                observed_destinations[label]["kind"] == "library_role"
                for label in ("Movies", "TV Shows")
            )
        )
        self.assertTrue(
            all(
                intended_destinations[label]["kind"] == "custom_window"
                for label in ("Movies", "TV Shows")
            )
        )
        self.assertEqual(
            deltas["movies_tv_destination"]["current"],
            "direct_library",
        )
        self.assertEqual(
            deltas["movies_tv_destination"]["intended"],
            "fork_hub",
        )
        self.assertEqual(
            deltas["menu_provider"]["current"],
            self.source["menu_provider"],
        )
        self.assertEqual(
            deltas["menu_provider"]["intended"],
            self.intended["navigation"]["provider"],
        )
        self.assertEqual(
            deltas["home_rows_provider"]["current"],
            self.source["home_rows_provider"],
        )
        self.assertEqual(
            deltas["home_rows_provider"]["intended"],
            self.intended["home"]["rows_provider"],
        )
        self.assertEqual(
            deltas["generated_movies_tv_submenus"]["current"],
            self.source["generated_movies_tv_submenus"],
        )
        self.assertEqual(
            deltas["generated_movies_tv_submenus"]["intended"],
            not all(
                value == "disabled"
                for value in self.intended["navigation"][
                    "submenu_policy"
                ].values()
            ),
        )
        expected_bases = {
            "sidebar_right_inactive_destination": "checked_in_source",
            "movies_tv_destination": "deployed_observation",
            "menu_provider": "checked_in_source",
            "home_rows_provider": "checked_in_source",
            "generated_movies_tv_submenus": "checked_in_source",
        }
        for delta in deltas.values():
            self.assertEqual(
                set(delta),
                {"area", "basis", "current", "intended", "disposition"},
            )
            self.assertEqual(delta["basis"], expected_bases[delta["area"]])
            self.assertIn(
                delta["disposition"],
                {
                    "replace_at_declarative_cutover",
                    "remove_at_declarative_cutover",
                },
            )

    def test_json_is_strict_and_byte_canonical(self):
        self.assertLessEqual(CONTRACT_PATH.stat().st_size, MAX_CONTRACT_BYTES)
        canonical = (json.dumps(self.contract, indent=2) + "\n").encode()
        self.assertEqual(CONTRACT_PATH.read_bytes(), canonical)
        invalid_documents = (
            '{"schema_version":1,"schema_version":1}',
            '{"schema_version":NaN}',
            '{"schema_version":Infinity}',
            "[]",
        )
        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaises(ValueError):
                    _loads_contract(document)

        private_values = (
            {"access_token": "secret"},
            {"path": "/Users/example/.kodi"},
            {"endpoint": "plugin://private"},
            {"address": "192.168.1.20"},
            {"id": "123e4567-e89b-12d3-a456-426614174000"},
        )
        for value in private_values:
            with self.subTest(value=value):
                with self.assertRaises(AssertionError):
                    _assert_no_private_state(value)


class TransitionalHomeSourceTest(unittest.TestCase):
    """Update each boundary assertion when its dependency is removed."""

    @classmethod
    def setUpClass(cls):
        cls.contract = _load_contract()
        cls.observed = cls.contract["deployed_observation"]
        cls.source = cls.contract["transitional_source_snapshot"]
        cls.intended = cls.contract["intended_declarative_contract"]
        cls.home = _parse(HOME_XML)
        cls.includes = _parse(INCLUDES_XML)
        cls.bingie = _parse(BINGIE_XML)
        cls.home_bingie = _parse(HOME_BINGIE_XML)
        cls.paths = _parse(PATHS_XML)
        cls.skin_settings = _parse(SKIN_SETTINGS_XML)
        cls.main_menu = _parse(MAIN_MENU_XML)
        cls.home_rows = _parse(HOME_ROWS_XML)

    def test_skin_shortcuts_boundary_is_explicit_and_generated_file_is_absent(self):
        addon = _parse(ADDON_XML)
        imports = [
            node
            for node in addon.findall("./requires/import")
            if node.get("addon") == "script.skinshortcuts"
        ]
        self.assertEqual(len(imports), 1)
        self.assertEqual(imports[0].get("version"), "2.0.3")
        self.assertEqual(self.source["menu_provider"], "skin_shortcuts")
        self.assertEqual(self.source["home_rows_provider"], "skin_shortcuts")

        build_actions = [
            _text(action)
            for action in self.home.findall("onload")
            if _text(action).startswith(
                "RunScript(script.skinshortcuts,type=buildxml&"
            )
        ]
        settings_actions = [
            _text(action)
            for action in self.skin_settings.findall("onunload")
            if _text(action).startswith(
                "RunScript(script.skinshortcuts,type=buildxml&"
            )
        ]
        self.assertEqual(len(build_actions), 1)
        self.assertEqual(settings_actions, build_actions)
        build_action = build_actions[0]
        self.assertIn("mainmenuID=900", build_action)
        self.assertIn("levels=1", build_action)
        group_match = re.search(r"(?:^|&)group=([^&)\s]+)", build_action)
        self.assertIsNotNone(group_match)
        self.assertEqual(
            group_match.group(1).split("|"),
            self.source["skin_shortcuts_build_groups"],
        )

        generated_file = self.observed["evidence"]["generated_include"]["name"]
        self.assertEqual(
            [
                node.get("file")
                for node in self.includes.findall("include")
                if node.get("file") == generated_file
            ],
            [generated_file],
        )
        self.assertFalse((XML_ROOT / generated_file).exists())

    def test_current_home_menu_and_rows_still_depend_on_generated_content(self):
        main_menu = _definition(self.bingie, "MainMenuContent")
        self.assertEqual(
            [_text(node) for node in main_menu.iter("include") if _text(node)],
            ["skinshortcuts-mainmenu"],
        )

        widget_group = _control(
            self.home_bingie,
            self.observed["controls"]["widget_group"],
        )
        home_templates = [
            _text(node)
            for node in widget_group.findall("include")
            if node.get("condition") == "Window.IsActive(Home)"
        ]
        self.assertEqual(home_templates, ["skinshortcuts-template-Widgets"])

        row_variables = [
            (_text(row.find("label")), _text(row.find("action")))
            for row in self.home_rows.findall("shortcut")
        ]
        self.assertEqual(row_variables, DEFAULT_WIDGET_VARIABLES)
        defined_variables = {
            variable.get("name")
            for variable in self.paths.findall("variable")
        }
        for name_pair in DEFAULT_WIDGET_VARIABLES:
            for expression in name_pair:
                name = expression.removeprefix("$VAR[").removesuffix("]")
                self.assertIn(name, defined_variables)

    def test_current_sidebar_right_route_matches_source_and_live_trace(self):
        controls = self.observed["controls"]
        self.assertEqual(
            _text(self.home.find("defaultcontrol")),
            str(self.source["home_bootstrap_control"]),
        )
        self.assertEqual(
            _text(self.home.find("menucontrol")),
            str(controls["sidebar"]),
        )
        bootstrap = _control(
            self.home,
            self.source["home_bootstrap_control"],
        )
        self.assertIn(
            f"SetFocus({controls['widget_group']})",
            _action_texts(bootstrap, "onfocus"),
        )

        sidebar = _control(self.bingie, controls["sidebar"])
        right_actions = _action_texts(sidebar, "onright")
        self.assertEqual(
            self.source["sidebar_right_source_action"],
            "clear_submenu_then_focus_widget_group",
        )
        self.assertIn("ClearProperty(ShowViewSubMenu,Home)", right_actions)
        self.assertIn(str(controls["widget_group"]), right_actions)
        self.assertFalse(
            any(re.search(r"(?:SetFocus\()?4444", action) for action in right_actions)
        )
        for trace in self.observed["interaction_traces"]["sidebar_right"]:
            self.assertEqual(
                trace["result_focus_group"],
                controls["widget_group"],
            )
            self.assertFalse(trace["submenu_visible"])

    def test_spotlight_entry_defaults_to_play_and_right_selects_more_info(self):
        controls = self.observed["controls"]
        trace = self.observed["home_entry_trace"]
        self.assertEqual(trace["focus"], "spotlight_first_item")
        self.assertTrue(trace["item_ready"])
        self.assertEqual(trace["spotlight_info"], "empty")
        self.assertEqual(
            self.intended["home"]["default_focus"]["primary_state"],
            "play",
        )
        self.assertEqual(
            self.intended["home"]["default_focus"][
                "activation_by_media_type"
            ],
            {
                "playable_video": "play_resume",
                "tvshow": "open_show",
                "music_container": "open_music",
            },
        )

        widget_group = _control(
            self.home_bingie,
            controls["widget_group"],
        )
        spotlight_consumers = [
            node
            for node in widget_group.findall("include")
            if node.get("content") == "BingieSpotlightWidget"
        ]
        self.assertEqual(len(spotlight_consumers), 1)
        parameters = {
            node.get("name"): node.get("value")
            for node in spotlight_consumers[0].findall("param")
        }
        self.assertEqual(parameters["widgetid"], str(controls["spotlight"]))

        definition = _definition(self.bingie, "BingieSpotlightWidget")
        spotlight_controls = definition.findall("control")
        self.assertEqual(len(spotlight_controls), 1)
        spotlight = spotlight_controls[0]
        self.assertIn(
            "ClearProperty(spotlightinfo,Home)",
            _action_texts(spotlight, "onfocus"),
        )

        right_actions = _actions(spotlight, "onright")
        self.assertEqual(len(right_actions), 1)
        self.assertEqual(
            _text(right_actions[0]),
            "SetProperty(spotlightinfo,1,Home)",
        )
        self.assertIn(
            "String.IsEmpty(Window(Home).Property(spotlightinfo))",
            right_actions[0].get("condition", ""),
        )

        clicks = _actions(spotlight, "onclick")
        info_clicks = [
            action for action in clicks if _text(action) == "Action(info)"
        ]
        self.assertEqual(len(info_clicks), 1)
        self.assertIn(
            "!String.IsEmpty(Window(Home).Property(spotlightinfo))",
            info_clicks[0].get("condition", ""),
        )
        play_clicks = [
            action for action in clicks if _text(action).startswith("PlayMedia(")
        ]
        self.assertEqual(len(play_clicks), 1)
        play_condition = play_clicks[0].get("condition", "")
        self.assertIn(
            "String.IsEmpty(Window(Home).Property(spotlightinfo))",
            play_condition,
        )
        self.assertIn(
            "!String.IsEqual(Container($PARAM[widgetid]).ListItem.DBType,tvshow)",
            play_condition,
        )
        tvshow_clicks = [
            action
            for action in clicks
            if _text(action).startswith("ActivateWindow(Videos,")
            and "ListItem.DBType,tvshow" in action.get("condition", "")
        ]
        self.assertEqual(len(tvshow_clicks), 2)
        music_clicks = [
            action
            for action in clicks
            if _text(action).startswith("ActivateWindow(Music,")
        ]
        self.assertEqual(len(music_clicks), 1)
        self.assertRegex(
            music_clicks[0].get("condition", ""),
            r"DBType,(?:album|artist)",
        )

    def test_checked_in_default_menu_is_snapshot_data_not_live_evidence(self):
        records = [
            {
                "label": _text(shortcut.find("label")),
                "default_id": (
                    _text(shortcut.find("defaultID"))
                    if shortcut.find("defaultID") is not None
                    else None
                ),
                "action": _text(shortcut.find("action")),
            }
            for shortcut in self.main_menu.findall("shortcut")
        ]
        self.assertEqual(
            records,
            self.source["checked_in_default_mainmenu"],
        )

    def test_genre_extensions_are_wired_to_library_backed_lists(self):
        controls = self.observed["controls"]
        widget_group = _control(
            self.home_bingie,
            controls["widget_group"],
        )
        direct_includes = list(widget_group.findall("include"))
        generic = _definition(self.home_bingie, "HTPC_Genre_Row")
        generic_control = generic.find("./definition/control")
        self.assertIsNotNone(generic_control)
        self.assertEqual(generic_control.get("type"), "fixedlist")
        navigation_factories = [
            node.get("content")
            for node in generic_control.findall("include")
        ]
        self.assertIn("Fixed_Focus_Navigation_Factory", navigation_factories)
        content = generic_control.find("content")
        self.assertEqual(content.get("target"), "videos")
        self.assertEqual(_text(content), "$PARAM[widgetPath]")

        definitions = {
            "movies": "HTPC_Movie_Genres_Row",
            "tvshows": "HTPC_TV_Genres_Row",
        }
        for role, include_name in definitions.items():
            with self.subTest(role=role):
                hub = self.intended["library_hubs"][role]
                consumers = [
                    node
                    for node in direct_includes
                    if _text(node) == include_name
                ]
                self.assertEqual(len(consumers), 1)
                self.assertEqual(
                    consumers[0].get("condition"),
                    f"Window.IsActive({hub['window']})",
                )

                definition = _definition(self.home_bingie, include_name)
                variants = definition.findall("include")
                self.assertEqual(len(variants), 3)
                for variant in variants:
                    parameters = {
                        node.get("name"): node.get("value")
                        for node in variant.findall("param")
                    }
                    self.assertEqual(
                        parameters["widgetid"],
                        str(hub["genre_row"]["id"]),
                    )
                    self.assertEqual(
                        parameters["widgetPath"],
                        hub["genre_row"]["path"],
                    )

                owners = sorted(
                    XML_ROOT.glob(f"Custom_{hub['window']}_*.xml")
                )
                self.assertEqual(len(owners), 1)
                hub_root = _parse(owners[0])
                self.assertEqual(hub_root.get("id"), str(hub["window"]))
                self.assertEqual(
                    _text(hub_root.find("defaultcontrol")),
                    str(controls["widget_group"]),
                )


if __name__ == "__main__":
    unittest.main()
