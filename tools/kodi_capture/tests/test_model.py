from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, replace

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
    ActivePlayer,
    AddonVersion,
    CaptureSettings,
    Control,
    HomeInvariantError,
    KodiObservation,
    SelectedItem,
    Window,
    validate_addon_versions,
    validate_home_observation,
)


def readiness_labels(**overrides):
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
    values.update(overrides)
    return tuple(sorted(values.items()))


def readiness_booleans(**overrides):
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
    values.update(overrides)
    return tuple(sorted(values.items()))


def home_observation(**overrides):
    values = {
        "window": Window(10000, "Home"),
        "control": Control(500, "Movies"),
        "selected_item": SelectedItem(
            "Movie",
            "movie",
            42,
            "/media/",
            "/media/movie.mkv",
            "provider-42",
        ),
        "active_players": (),
        "skin_id": "skin.bingie",
        "skin_name": "Bingie",
        "fullscreen": False,
        "readiness_labels": readiness_labels(),
        "readiness_booleans": readiness_booleans(),
    }
    values.update(overrides)
    return KodiObservation(**values)


class ImmutableModelTest(unittest.TestCase):
    def test_values_are_frozen(self):
        values = [
            Window(10000, "Home"),
            Control(500, "Movies"),
            SelectedItem("Movie", "movie", 42, "/media/", "/m.mkv", "u"),
            ActivePlayer(1, "video"),
            home_observation(),
            CaptureSettings("/tmp/shots", "dim", 5, 0),
            AddonVersion("skin.bingie", "1.0", True, True, "skin"),
        ]
        for value in values:
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(FrozenInstanceError):
                    value.unexpected = True

    def test_bool_is_not_accepted_as_an_integer(self):
        constructors = [
            lambda: Window(True, "Home"),
            lambda: Control(True, "Movies"),
            lambda: SelectedItem("Movie", "movie", True, "", "", ""),
            lambda: ActivePlayer(False, "video"),
            lambda: CaptureSettings("/tmp/shots", "dim", True, 0),
            lambda: CaptureSettings("/tmp/shots", "dim", 5, False),
        ]
        for constructor in constructors:
            with self.subTest(constructor=constructor):
                with self.assertRaises(TypeError):
                    constructor()

    def test_wrong_scalar_and_container_types_are_rejected(self):
        with self.assertRaises(TypeError):
            Window(10000, None)
        with self.assertRaises(TypeError):
            AddonVersion("id", "1", 1, True, "type")
        with self.assertRaises(TypeError):
            home_observation(active_players=[])
        with self.assertRaises(TypeError):
            home_observation(readiness_labels=list(readiness_labels()))
        with self.assertRaises(TypeError):
            home_observation(
                readiness_booleans=((BUSY_VISIBLE, "false"),)
            )

    def test_readiness_keys_must_be_unique_sorted_and_complete(self):
        labels = readiness_labels()
        booleans = readiness_booleans()
        invalid = [
            {"readiness_labels": tuple(reversed(labels))},
            {"readiness_labels": labels + (labels[-1],)},
            {"readiness_labels": labels[1:]},
            {"readiness_booleans": tuple(reversed(booleans))},
            {"readiness_booleans": booleans + (booleans[-1],)},
            {"readiness_booleans": booleans[1:]},
        ]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    home_observation(**overrides)

    def test_active_players_are_unique_and_sorted(self):
        with self.assertRaises(ValueError):
            home_observation(
                active_players=(
                    ActivePlayer(2, "video"),
                    ActivePlayer(1, "audio"),
                )
            )
        with self.assertRaises(ValueError):
            home_observation(
                active_players=(
                    ActivePlayer(1, "video"),
                    ActivePlayer(1, "audio"),
                )
            )

    def test_addon_versions_are_unique_sorted_and_frozen(self):
        first = AddonVersion("a", "1", True, True, "script")
        second = AddonVersion("b", "2", False, True, "plugin")
        validate_addon_versions((first, second))
        for addons in ((second, first), (first, first)):
            with self.subTest(addons=addons):
                with self.assertRaises(ValueError):
                    validate_addon_versions(addons)
        with self.assertRaises(TypeError):
            validate_addon_versions([first])


class SelectedIdentityTest(unittest.TestCase):
    def test_identity_prefers_database_then_unique_id_then_paths(self):
        cases = [
            (
                SelectedItem("A", "movie", 7, "/folder", "/file", "uid"),
                ("database", "movie", "7"),
            ),
            (
                SelectedItem("A", "movie", None, "/folder", "/file", "uid"),
                ("unique", "movie", "uid"),
            ),
            (
                SelectedItem("A", "", None, "/folder", "/file", ""),
                ("file", "/file"),
            ),
            (
                SelectedItem("A", "", None, "/folder", "", ""),
                ("folder", "/folder"),
            ),
            (
                SelectedItem("Only a label", "", None, "", "", ""),
                None,
            ),
        ]
        for selected, expected in cases:
            with self.subTest(selected=selected):
                self.assertEqual(selected.strong_identity, expected)

    def test_database_id_requires_database_type(self):
        with self.assertRaises(ValueError):
            SelectedItem("A", "", 7, "", "", "")


class HomeInvariantTest(unittest.TestCase):
    def test_valid_home_allows_either_screensaver_state_and_optional_control(self):
        awake = home_observation()
        asleep = replace(
            awake,
            readiness_booleans=readiness_booleans(
                **{SCREENSAVER_ACTIVE: True}
            ),
        )
        no_control = replace(awake, control=Control(None, ""))
        for observation in (awake, asleep, no_control):
            validate_home_observation(observation)

    def test_focus_evidence_excludes_only_screensaver_and_dpms(self):
        baseline = home_observation()
        changed = replace(
            baseline,
            readiness_booleans=readiness_booleans(
                **{
                    SCREENSAVER_ACTIVE: True,
                    DPMS_ACTIVE: True,
                }
            ),
        )
        self.assertEqual(baseline.focus_evidence, changed.focus_evidence)

    def test_every_home_invariant_fails_closed(self):
        baseline = home_observation()
        weak_item = SelectedItem("Only a label", "", None, "", "", "")
        cases = [
            replace(baseline, window=Window(10001, "Videos")),
            replace(
                baseline,
                readiness_booleans=readiness_booleans(
                    **{HOME_ACTIVE: False}
                ),
            ),
            replace(baseline, skin_id="skin.estuary"),
            replace(baseline, fullscreen=True),
            replace(
                baseline,
                active_players=(ActivePlayer(1, "video"),),
            ),
            replace(
                baseline,
                readiness_booleans=readiness_booleans(
                    **{PLAYER_HAS_MEDIA: True}
                ),
            ),
            replace(
                baseline,
                readiness_booleans=readiness_booleans(
                    **{DPMS_ACTIVE: True}
                ),
            ),
            replace(
                baseline,
                readiness_booleans=readiness_booleans(
                    **{MODAL_ACTIVE: True}
                ),
            ),
            replace(
                baseline,
                readiness_booleans=readiness_booleans(
                    **{MODAL_PRESENT: True}
                ),
            ),
            replace(
                baseline,
                readiness_booleans=readiness_booleans(
                    **{BUSY_VISIBLE: True}
                ),
            ),
            replace(
                baseline,
                readiness_booleans=readiness_booleans(
                    **{BUSY_NO_CANCEL_VISIBLE: True}
                ),
            ),
            replace(
                baseline,
                readiness_labels=readiness_labels(
                    **{SERVICE_READY_LABEL: ""}
                ),
            ),
            replace(baseline, selected_item=weak_item),
        ]
        for observation in cases:
            with self.subTest(observation=observation):
                with self.assertRaises(HomeInvariantError):
                    validate_home_observation(observation)


if __name__ == "__main__":
    unittest.main()
