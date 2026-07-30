from __future__ import annotations

import inspect
import unittest

import tools.kodi_capture.kodi_read as kodi_read
from tools.kodi_capture.kodi_read import (
    CAPTURE_ADDON_IDS,
    CAPTURE_SETTING_IDS,
    FOCUS_FENCE_LABELS,
    GUI_PROPERTIES,
    KodiReadClient,
    KodiReadError,
)
from tools.kodi_capture.model import (
    BUSY_NO_CANCEL_VISIBLE,
    BUSY_VISIBLE,
    CURRENT_CONTROL_ID_LABEL,
    CURRENT_CONTROL_LABEL,
    CURRENT_WINDOW_LABEL,
    DPMS_ACTIVE,
    HOME_ACTIVE,
    MODAL_ACTIVE,
    MODAL_PRESENT,
    PLAYER_HAS_MEDIA,
    SCREENSAVER_ACTIVE,
    SELECTED_DB_ID,
    SELECTED_DB_TYPE,
    SELECTED_FILE_PATH,
    SELECTED_FOLDER_PATH,
    SELECTED_LABEL,
    SELECTED_UNIQUE_ID,
    SERVICE_READY_LABEL,
    STANDARD_READINESS_BOOLEANS,
    STANDARD_READINESS_LABELS,
)


class FakeRpc:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, method, params, *, deadline):
        self.calls.append((method, params, deadline))
        if not self.responses:
            raise AssertionError("unexpected JSON-RPC call")
        return self.responses.pop(0)


def gui_result(**overrides):
    result = {
        "currentwindow": {"id": 10000, "label": "Home"},
        "currentcontrol": {"label": "Movies"},
        "skin": {"id": "skin.bingie", "name": "Bingie"},
        "fullscreen": False,
    }
    result.update(overrides)
    return result


def boolean_result(extra=None, **overrides):
    values = {
        BUSY_NO_CANCEL_VISIBLE: False,
        BUSY_VISIBLE: False,
        DPMS_ACTIVE: False,
        HOME_ACTIVE: True,
        MODAL_ACTIVE: False,
        MODAL_PRESENT: False,
        PLAYER_HAS_MEDIA: False,
        SCREENSAVER_ACTIVE: False,
    }
    if extra is not None:
        values.update(extra)
    values.update(overrides)
    return values


def label_result(extra=None, **overrides):
    values = {
        CURRENT_CONTROL_ID_LABEL: "500",
        CURRENT_CONTROL_LABEL: "Movies",
        CURRENT_WINDOW_LABEL: "Home",
        SELECTED_DB_ID: "42",
        SELECTED_DB_TYPE: "movie",
        SELECTED_FILE_PATH: "/media/movie.mkv",
        SELECTED_FOLDER_PATH: "/media/",
        SELECTED_LABEL: "Movie",
        SELECTED_UNIQUE_ID: "provider-42",
        SERVICE_READY_LABEL: "true",
    }
    if extra is not None:
        values.update(extra)
    values.update(overrides)
    return values


def focus_result(**overrides):
    values = {
        CURRENT_CONTROL_ID_LABEL: "500",
        CURRENT_CONTROL_LABEL: "Movies",
        CURRENT_WINDOW_LABEL: "Home",
    }
    values.update(overrides)
    return values


def home_responses(
    *,
    before=None,
    before_focus=None,
    players=None,
    booleans=None,
    labels=None,
    after=None,
    after_focus=None,
):
    first_gui = gui_result() if before is None else before
    first_focus = (
        focus_result() if before_focus is None else before_focus
    )
    return [
        first_gui,
        first_focus,
        [] if players is None else players,
        boolean_result() if booleans is None else booleans,
        label_result() if labels is None else labels,
        first_gui if after is None else after,
        first_focus if after_focus is None else after_focus,
    ]


def addon_result(addon_id, *, limits=False, **overrides):
    addon = {
        "addonid": addon_id,
        "version": "1.2.3",
        "enabled": True,
        "installed": True,
        "type": "xbmc.addon",
    }
    addon.update(overrides)
    result = {"addon": addon}
    if limits:
        result["limits"] = {"start": 0, "end": 1, "total": 1}
    return result


class LiteralContractTest(unittest.TestCase):
    def test_live_kodi_info_literals_are_exact(self):
        self.assertEqual(
            STANDARD_READINESS_LABELS,
            (
                "ListItem.DBID",
                "ListItem.DBTYPE",
                "ListItem.FileNameAndPath",
                "ListItem.FolderPath",
                "ListItem.Label",
                "ListItem.UniqueID",
                "System.CurrentControl",
                "System.CurrentControlId",
                "System.CurrentWindow",
                "Window(Home).Property(htpc.service.ready)",
            ),
        )
        self.assertEqual(
            STANDARD_READINESS_BOOLEANS,
            (
                "Player.HasMedia",
                "System.DPMSActive",
                "System.HasActiveModalDialog",
                "System.HasModalDialog",
                "System.ScreenSaverActive",
                "Window.IsActive(Home)",
                "Window.IsVisible(busydialog)",
                "Window.IsVisible(busydialognocancel)",
            ),
        )
        self.assertEqual(
            FOCUS_FENCE_LABELS,
            (
                "System.CurrentControl",
                "System.CurrentControlId",
                "System.CurrentWindow",
            ),
        )


class KodiHomeReadTest(unittest.TestCase):
    def test_observe_home_uses_exact_fenced_reads_and_one_deadline(self):
        extra_label = "Container.FolderPath"
        extra_boolean = "Container.HasFocus"
        gui_with_extras = gui_result(
            currentwindow={
                "id": 10000,
                "label": "Home",
                "unrequested": "ignored",
            },
            currentcontrol={
                "label": "Movies",
                "unrequested": "ignored",
            },
            skin={
                "id": "skin.bingie",
                "name": "Bingie",
                "unrequested": "ignored",
            },
            unrequested="ignored",
        )
        rpc = FakeRpc(
            home_responses(
                before=gui_with_extras,
                after=gui_with_extras,
                booleans=boolean_result(
                    extra={
                        extra_boolean: True,
                        "Unrequested.Boolean": False,
                    },
                ),
                labels=label_result(
                    extra={
                        extra_label: "plugin://movies",
                        "Unrequested.Label": "ignored",
                    },
                ),
                before_focus={
                    **focus_result(),
                    "unrequested": "ignored",
                },
                after_focus={
                    **focus_result(),
                    "unrequested": "ignored",
                },
            )
        )
        client = KodiReadClient(rpc)

        observation = client.observe_home(
            123.5,
            extra_readiness_labels=(extra_label,),
            extra_readiness_booleans=(extra_boolean,),
        )

        expected_labels = tuple(
            sorted(STANDARD_READINESS_LABELS + (extra_label,))
        )
        expected_booleans = tuple(
            sorted(STANDARD_READINESS_BOOLEANS + (extra_boolean,))
        )
        self.assertEqual(
            rpc.calls,
            [
                (
                    "GUI.GetProperties",
                    {"properties": list(GUI_PROPERTIES)},
                    123.5,
                ),
                (
                    "XBMC.GetInfoLabels",
                    {"labels": list(FOCUS_FENCE_LABELS)},
                    123.5,
                ),
                ("Player.GetActivePlayers", {}, 123.5),
                (
                    "XBMC.GetInfoBooleans",
                    {"booleans": list(expected_booleans)},
                    123.5,
                ),
                (
                    "XBMC.GetInfoLabels",
                    {"labels": list(expected_labels)},
                    123.5,
                ),
                (
                    "GUI.GetProperties",
                    {"properties": list(GUI_PROPERTIES)},
                    123.5,
                ),
                (
                    "XBMC.GetInfoLabels",
                    {"labels": list(FOCUS_FENCE_LABELS)},
                    123.5,
                ),
            ],
        )
        self.assertEqual(observation.window.window_id, 10000)
        self.assertEqual(observation.control.control_id, 500)
        self.assertEqual(
            observation.selected_item.strong_identity,
            ("database", "movie", "42"),
        )
        self.assertEqual(
            observation.label_value(extra_label),
            "plugin://movies",
        )
        self.assertTrue(observation.boolean_value(extra_boolean))

    def test_active_players_are_sorted_but_home_rejects_any_player(self):
        rpc = FakeRpc(
            home_responses(
                players=[
                    {
                        "playerid": 2,
                        "type": "picture",
                        "unrequested": "ignored",
                    },
                    {
                        "playerid": 1,
                        "type": "video",
                        "unrequested": "ignored",
                    },
                ]
            )
        )
        with self.assertRaisesRegex(
            ValueError,
            "active player exists",
        ):
            KodiReadClient(rpc).observe_home(5.0)

    def test_gui_fence_rejects_mid_observation_change(self):
        rpc = FakeRpc(
            home_responses(
                after=gui_result(
                    currentcontrol={"label": "TV Shows"},
                ),
                after_focus=focus_result(
                    **{CURRENT_CONTROL_LABEL: "TV Shows"}
                ),
            )
        )
        with self.assertRaisesRegex(KodiReadError, "focus changed"):
            KodiReadClient(rpc).observe_home(5.0)

    def test_focus_fence_rejects_changed_same_label_control_id(self):
        rpc = FakeRpc(
            home_responses(
                after_focus=focus_result(
                    **{CURRENT_CONTROL_ID_LABEL: "501"}
                )
            )
        )
        with self.assertRaisesRegex(KodiReadError, "focus changed"):
            KodiReadClient(rpc).observe_home(5.0)

    def test_middle_control_id_must_match_fenced_focus(self):
        rpc = FakeRpc(
            home_responses(
                labels=label_result(
                    **{CURRENT_CONTROL_ID_LABEL: "501"}
                )
            )
        )
        with self.assertRaisesRegex(KodiReadError, "control IDs"):
            KodiReadClient(rpc).observe_home(5.0)

    def test_info_labels_must_agree_with_gui_fence(self):
        mismatches = [
            {CURRENT_WINDOW_LABEL: "Videos"},
            {CURRENT_CONTROL_LABEL: "TV Shows"},
        ]
        for mismatch in mismatches:
            with self.subTest(mismatch=mismatch):
                rpc = FakeRpc(
                    home_responses(
                        labels=label_result(**mismatch),
                    )
                )
                with self.assertRaises(KodiReadError):
                    KodiReadClient(rpc).observe_home(5.0)

    def test_extra_readiness_keys_must_be_unique_sorted_and_disjoint(self):
        invalid = [
            {"extra_readiness_labels": ["A"]},
            {"extra_readiness_labels": ("B", "A")},
            {"extra_readiness_labels": ("A", "A")},
            {
                "extra_readiness_labels": (
                    CURRENT_WINDOW_LABEL,
                )
            },
            {"extra_readiness_booleans": ("B", "A")},
            {"extra_readiness_booleans": (HOME_ACTIVE,)},
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                rpc = FakeRpc([])
                with self.assertRaises((TypeError, ValueError)):
                    KodiReadClient(rpc).observe_home(5.0, **arguments)
                self.assertEqual(rpc.calls, [])

    def test_result_shapes_and_scalar_types_are_strict(self):
        cases = []

        gui_missing = gui_result()
        del gui_missing["fullscreen"]
        cases.append(home_responses(before=gui_missing, after=gui_missing))

        cases.append(
            home_responses(
                players=[{"playerid": True, "type": "video"}],
            )
        )
        cases.append(
            home_responses(
                players=[{"playerid": 1}],
            )
        )

        missing_boolean = boolean_result()
        del missing_boolean[HOME_ACTIVE]
        cases.append(home_responses(booleans=missing_boolean))

        wrong_boolean = boolean_result()
        wrong_boolean[HOME_ACTIVE] = 1
        cases.append(home_responses(booleans=wrong_boolean))

        missing_label = label_result()
        del missing_label[SELECTED_LABEL]
        cases.append(home_responses(labels=missing_label))

        wrong_label = label_result()
        wrong_label[SELECTED_LABEL] = 7
        cases.append(home_responses(labels=wrong_label))

        cases.append(
            home_responses(
                labels=label_result(
                    **{CURRENT_CONTROL_ID_LABEL: "-1"}
                )
            )
        )
        cases.append(
            home_responses(
                labels=label_result(**{SELECTED_DB_ID: "false"})
            )
        )
        cases.append(
            home_responses(
                labels=label_result(
                    **{
                        SELECTED_DB_ID: "42",
                        SELECTED_DB_TYPE: "",
                    }
                )
            )
        )

        for responses in cases:
            with self.subTest(responses=responses):
                with self.assertRaises(KodiReadError):
                    KodiReadClient(FakeRpc(responses)).observe_home(5.0)

    def test_observation_is_canonical_despite_raw_map_order(self):
        normal = KodiReadClient(
            FakeRpc(home_responses())
        ).observe_home(5.0)

        reversed_gui = {
            key: (
                dict(reversed(tuple(value.items())))
                if isinstance(value, dict)
                else value
            )
            for key, value in reversed(tuple(gui_result().items()))
        }
        reversed_focus = dict(
            reversed(tuple(focus_result().items()))
        )
        reversed_booleans = dict(
            reversed(tuple(boolean_result().items()))
        )
        reversed_labels = dict(
            reversed(tuple(label_result().items()))
        )
        reordered = KodiReadClient(
            FakeRpc(
                home_responses(
                    before=reversed_gui,
                    before_focus=reversed_focus,
                    booleans=reversed_booleans,
                    labels=reversed_labels,
                    after=reversed_gui,
                    after_focus=reversed_focus,
                )
            )
        ).observe_home(5.0)

        self.assertEqual(normal, reordered)
        self.assertEqual(hash(normal), hash(reordered))

    def test_wrong_home_state_is_rejected_after_valid_read(self):
        rpc = FakeRpc(
            home_responses(
                booleans=boolean_result(
                    **{DPMS_ACTIVE: True},
                )
            )
        )
        with self.assertRaisesRegex(ValueError, "DPMS is active"):
            KodiReadClient(rpc).observe_home(5.0)


class KodiCaptureSettingsTest(unittest.TestCase):
    def test_reads_exact_settings_with_one_deadline(self):
        values = [
            "/tmp/kodi-screenshots",
            "screensaver.xbmc.builtin.dim",
            5,
            0,
        ]
        rpc = FakeRpc(
            [
                {"value": value, "unrequested": "ignored"}
                for value in values
            ]
        )

        settings = KodiReadClient(rpc).read_capture_settings(44.0)

        self.assertEqual(settings.screenshot_path, values[0])
        self.assertEqual(settings.screensaver_mode, values[1])
        self.assertEqual(settings.screensaver_time, 5)
        self.assertEqual(settings.display_off_minutes, 0)
        self.assertEqual(
            rpc.calls,
            [
                (
                    "Settings.GetSettingValue",
                    {"setting": setting},
                    44.0,
                )
                for setting in CAPTURE_SETTING_IDS
            ],
        )

    def test_setting_shapes_and_types_are_strict(self):
        valid = [
            {"value": "/tmp/kodi-screenshots"},
            {"value": "screensaver.xbmc.builtin.dim"},
            {"value": 5},
            {"value": 0},
        ]
        replacements = [
            (0, {"unexpected": True}),
            (0, {"value": ""}),
            (1, {"value": 7}),
            (2, {"value": True}),
            (3, {"value": -1}),
        ]
        for index, replacement in replacements:
            with self.subTest(index=index, replacement=replacement):
                responses = list(valid)
                responses[index] = replacement
                with self.assertRaises(KodiReadError):
                    KodiReadClient(
                        FakeRpc(responses)
                    ).read_capture_settings(5.0)


class KodiAddonReadTest(unittest.TestCase):
    def test_reads_exact_canonical_addons_with_one_deadline(self):
        rpc = FakeRpc(
            [
                {
                    **addon_result(
                        addon_id,
                        limits=index == 0,
                        detail_extra="ignored",
                    ),
                    "wrapper_extra": "ignored",
                }
                for index, addon_id in enumerate(CAPTURE_ADDON_IDS)
            ]
        )
        rpc.responses[0]["addon"]["enabled"] = False

        addons = KodiReadClient(rpc).read_addons(70.0)

        self.assertEqual(
            tuple(addon.addon_id for addon in addons),
            CAPTURE_ADDON_IDS,
        )
        self.assertFalse(addons[0].enabled)
        self.assertTrue(addons[0].installed)
        self.assertTrue(all(addon.enabled for addon in addons[1:]))
        self.assertTrue(all(addon.installed for addon in addons))
        self.assertEqual(
            rpc.calls,
            [
                (
                    "Addons.GetAddonDetails",
                    {
                        "addonid": addon_id,
                        "properties": [
                            "version",
                            "enabled",
                            "installed",
                        ],
                    },
                    70.0,
                )
                for addon_id in CAPTURE_ADDON_IDS
            ],
        )

    def test_addon_shapes_ids_and_types_are_strict(self):
        valid = [
            addon_result(addon_id)
            for addon_id in CAPTURE_ADDON_IDS
        ]
        invalid_first = [
            {"addon": {}},
            addon_result(CAPTURE_ADDON_IDS[0], enabled=1),
            addon_result(CAPTURE_ADDON_IDS[0], version=""),
            addon_result("wrong.addon"),
            {"unexpected": True},
        ]
        for first in invalid_first:
            with self.subTest(first=first):
                responses = list(valid)
                responses[0] = first
                with self.assertRaises(KodiReadError):
                    KodiReadClient(FakeRpc(responses)).read_addons(5.0)


class ReadOnlySurfaceTest(unittest.TestCase):
    def test_client_exposes_only_declared_reads(self):
        public_methods = {
            name
            for name, value in KodiReadClient.__dict__.items()
            if callable(value) and not name.startswith("_")
        }
        self.assertEqual(
            public_methods,
            {
                "observe_home",
                "read_capture_settings",
                "read_addons",
            },
        )

    def test_source_contains_no_mutating_kodi_method(self):
        source = inspect.getsource(kodi_read)
        for forbidden in (
            "Input.",
            "Settings.Set",
            "Settings.Reset",
            "GUI.ActivateWindow",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
