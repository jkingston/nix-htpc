from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

from review_contract import CLEANUP_PROPERTY_KEYS, EXPECTED_FOCUS


ROOT = Path(__file__).resolve().parent


class FakeWindow:
    def __init__(self, all_events):
        self.values = {}
        self.events = []
        self.all_events = all_events

    def setProperty(self, key, value):
        self.values[key] = value
        self.events.append(("set", key, value))
        self.all_events.append(("set", key, value))

    def clearProperty(self, key):
        self.values.pop(key, None)
        self.events.append(("clear", key))
        self.all_events.append(("clear", key))

    def getProperty(self, key):
        return self.values.get(key, "")


class RuntimeHarness:
    def __init__(
        self,
        skin="skin.bingie",
        media=False,
        review_active=False,
        current_window=10000,
        current_dialog=None,
        builtin_failure=None,
        busy=False,
        screensaver=False,
        dpms=False,
        drift_on_wait=None,
    ):
        self.skin = skin
        self.media = media
        self.review_active = review_active
        self.current_window = current_window
        self.current_dialog = (
            11192 if review_active else 9999
            if current_dialog is None
            else current_dialog
        )
        self.builtin_failure = builtin_failure
        self.busy = busy
        self.screensaver = screensaver
        self.dpms = dpms
        self.drift_on_wait = drift_on_wait
        self.wait_count = 0
        self.current_focus = "23007"
        self.all_events = []
        self.window = FakeWindow(self.all_events)
        self.builtins = []
        self.logs = []
        self.sleeps = []

        xbmc = types.ModuleType("xbmc")
        xbmc.LOGERROR = 4
        xbmc.getInfoLabel = self.get_info_label
        xbmc.getSkinDir = lambda: self.skin
        xbmc.getCondVisibility = self.get_condition
        xbmc.executebuiltin = self.execute_builtin
        xbmc.sleep = self.sleep
        xbmc.log = lambda message, level: self.logs.append((message, level))
        xbmc.Monitor = lambda: self

        xbmcgui = types.ModuleType("xbmcgui")
        xbmcgui.Window = self.get_window
        xbmcgui.getCurrentWindowId = lambda: self.current_window
        xbmcgui.getCurrentWindowDialogId = lambda: self.current_dialog

        sys.modules["xbmc"] = xbmc
        sys.modules["xbmcgui"] = xbmcgui
        spec = importlib.util.spec_from_file_location(
            "osd_review_runtime_under_test",
            ROOT / "default.py",
        )
        self.runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runtime)

    def get_info_label(self, label):
        self.assert_equal(label, "System.CurrentControlId")
        return self.current_focus

    def get_condition(self, condition):
        if condition == "Player.HasMedia":
            return self.media
        if condition == "Window.IsActive(1192)":
            return self.review_active
        if condition == "System.ScreenSaverActive":
            return self.screensaver
        if condition == "System.DPMSActive":
            return self.dpms
        if condition in (
            "Window.IsActive(busydialog)",
            "Window.IsActive(busydialognocancel)",
        ):
            return self.busy
        raise AssertionError("unexpected condition: " + condition)

    def execute_builtin(self, command):
        self.builtins.append(command)
        self.all_events.append(("builtin", command))
        if command == self.builtin_failure:
            raise RuntimeError("injected builtin failure")
        if command == "Dialog.Close(1192,true)":
            self.review_active = False
            self.current_window = 10000
            self.current_dialog = 9999
        elif command == "ActivateWindow(1192)":
            self.review_active = True
            self.current_window = 10000
            self.current_dialog = 11192
            scenario = self.window.values["htpc.review.scenario"]
            self.current_focus = EXPECTED_FOCUS[scenario]
        else:
            raise AssertionError("unexpected builtin: " + command)

    def sleep(self, milliseconds):
        self.sleeps.append(milliseconds)

    def waitForAbort(self, seconds):
        self.sleeps.append(seconds)
        self.wait_count += 1
        if self.drift_on_wait is not None and self.wait_count == 1:
            self.drift_on_wait(self)
            return False
        return True

    def get_window(self, window_id):
        self.assert_equal(window_id, 10000)
        return self.window

    @staticmethod
    def assert_equal(actual, expected):
        if actual != expected:
            raise AssertionError("%r != %r" % (actual, expected))


class ReviewRuntimeTest(unittest.TestCase):
    def test_state_clears_then_publishes_ready_last_and_opens_exact_window(self):
        harness = RuntimeHarness()
        self.assertTrue(harness.runtime.run(["state=seek-forward"]))
        self.assertEqual(
            harness.builtins,
            ["ActivateWindow(1192)", "Dialog.Close(1192,true)"],
        )
        ready_event = ("set", "htpc.review.ready", "true")
        ready_index = harness.window.events.index(ready_event)
        self.assertEqual(
            harness.window.events[ready_index - 1],
            (
                "set",
                "htpc.review.seek.viewactive",
                "true",
            ),
        )
        self.assertEqual(harness.window.values, {})
        self.assertLess(
            harness.all_events.index(("builtin", "ActivateWindow(1192)")),
            harness.all_events.index(ready_event),
        )
        self.assertEqual(
            harness.window.events[-len(CLEANUP_PROPERTY_KEYS) :],
            [("clear", key) for key in CLEANUP_PROPERTY_KEYS],
        )

    def test_slot_b_frame_is_complete_before_atomic_publication(self):
        harness = RuntimeHarness()
        self.assertTrue(
            harness.runtime.run(["state=seek-forward-slot-b"])
        )
        preview = (
            "set",
            "htpc.review.seek.b.previewanchor",
            "75",
        )
        select = ("set", "htpc.review.seek.viewslot", "b")
        expose = ("set", "htpc.review.seek.viewactive", "true")
        activate = ("builtin", "ActivateWindow(1192)")
        self.assertLess(
            harness.all_events.index(preview),
            harness.all_events.index(select),
        )
        self.assertLess(
            harness.all_events.index(select),
            harness.all_events.index(expose),
        )
        self.assertLess(
            harness.all_events.index(expose),
            harness.all_events.index(activate),
        )

    def test_modal_fence_precedes_atomic_view_publication(self):
        harness = RuntimeHarness()
        self.assertTrue(
            harness.runtime.run(["state=seek-forward-modal"])
        )
        modal = ("set", "htpc.review.seek.modal", "true")
        select = ("set", "htpc.review.seek.viewslot", "a")
        expose = ("set", "htpc.review.seek.viewactive", "true")
        activate = ("builtin", "ActivateWindow(1192)")
        self.assertLess(
            harness.all_events.index(modal),
            harness.all_events.index(select),
        )
        self.assertLess(
            harness.all_events.index(select),
            harness.all_events.index(expose),
        )
        self.assertLess(
            harness.all_events.index(expose),
            harness.all_events.index(activate),
        )

    def test_playing_transport_keeps_paused_property_absent(self):
        harness = RuntimeHarness()
        self.assertTrue(harness.runtime.run(["state=transport-playing"]))
        self.assertEqual(harness.current_focus, "9201")
        self.assertFalse(
            any(
                event[:2] == ("set", "htpc.review.paused")
                for event in harness.window.events
            )
        )
        ready = ("set", "htpc.review.ready", "true")
        self.assertLess(
            harness.all_events.index(("builtin", "ActivateWindow(1192)")),
            harness.all_events.index(ready),
        )
        self.assertEqual(harness.window.values, {})

    def test_open_refuses_an_existing_review_without_mutation(self):
        harness = RuntimeHarness(review_active=True)
        self.assertFalse(harness.runtime.run(["state=transport-paused"]))
        self.assertEqual(harness.builtins, [])
        self.assertEqual(harness.window.events, [])

    def test_close_clears_every_property_and_does_not_open(self):
        harness = RuntimeHarness(review_active=True)
        harness.window.values = {
            key: "stale" for key in CLEANUP_PROPERTY_KEYS
        }
        self.assertTrue(harness.runtime.run(["command=close"]))
        self.assertEqual(harness.builtins, ["Dialog.Close(1192,true)"])
        self.assertEqual(harness.window.values, {})
        self.assertEqual(
            harness.window.events,
            [("clear", "htpc.review.ready")]
            + [("clear", key) for key in CLEANUP_PROPERTY_KEYS],
        )

    def test_active_media_refuses_without_ui_or_property_mutation(self):
        harness = RuntimeHarness(media=True)
        self.assertFalse(harness.runtime.run(["state=timeline-idle"]))
        self.assertEqual(harness.builtins, [])
        self.assertEqual(harness.window.events, [])
        self.assertIn("idle BINGIE Home", harness.logs[-1][0])

    def test_other_skin_refuses_before_accessing_window(self):
        harness = RuntimeHarness(skin="Estuary")
        harness.get_window = lambda _window_id: self.fail(
            "Home window must not be touched under another skin"
        )
        self.assertFalse(harness.runtime.run(["state=timeline-idle"]))
        self.assertEqual(harness.builtins, [])
        self.assertEqual(harness.window.events, [])
        self.assertIn("not BINGIE", harness.logs[-1][0])

    def test_non_home_window_refuses_without_mutation(self):
        harness = RuntimeHarness(current_window=12005)
        self.assertFalse(harness.runtime.run(["state=timeline-idle"]))
        self.assertEqual(harness.builtins, [])
        self.assertEqual(harness.window.events, [])

    def test_existing_modal_or_busy_dialog_refuses_without_mutation(self):
        for harness in (
            RuntimeHarness(current_dialog=12005),
            RuntimeHarness(busy=True),
        ):
            with self.subTest(
                dialog=harness.current_dialog,
                busy=harness.busy,
            ):
                self.assertFalse(
                    harness.runtime.run(["state=timeline-idle"])
                )
                self.assertEqual(harness.builtins, [])
                self.assertEqual(harness.window.events, [])

    def test_stale_review_state_requires_explicit_cleanup(self):
        harness = RuntimeHarness()
        harness.window.values["htpc.review.ready"] = "stale"
        self.assertFalse(harness.runtime.run(["state=timeline-idle"]))
        self.assertEqual(harness.builtins, [])
        self.assertEqual(harness.window.events, [])

    def test_activation_failure_still_revokes_and_clears_fixture(self):
        harness = RuntimeHarness(builtin_failure="ActivateWindow(1192)")
        self.assertFalse(harness.runtime.run(["state=seek-forward"]))
        self.assertEqual(
            harness.builtins,
            ["ActivateWindow(1192)"],
        )
        self.assertEqual(harness.window.values, {})
        self.assertEqual(
            harness.window.events[-len(CLEANUP_PROPERTY_KEYS) :],
            [("clear", key) for key in CLEANUP_PROPERTY_KEYS],
        )

    def test_ready_state_drift_aborts_and_cleans(self):
        def activate_screensaver(harness):
            harness.screensaver = True

        harness = RuntimeHarness(drift_on_wait=activate_screensaver)
        self.assertFalse(harness.runtime.run(["state=seek-forward"]))
        self.assertEqual(
            harness.builtins,
            ["ActivateWindow(1192)", "Dialog.Close(1192,true)"],
        )
        self.assertEqual(harness.window.values, {})
        self.assertIn("state drifted", harness.logs[-1][0])

    def test_close_failure_still_attempts_every_property_clear(self):
        harness = RuntimeHarness(
            review_active=True,
            builtin_failure="Dialog.Close(1192,true)",
        )
        harness.window.values = {
            key: "stale" for key in CLEANUP_PROPERTY_KEYS
        }
        self.assertFalse(harness.runtime.run(["command=close"]))
        self.assertEqual(
            harness.builtins,
            ["Dialog.Close(1192,true)"] * 3,
        )
        self.assertEqual(harness.window.values, {})
        self.assertIn("cleanup failed", harness.logs[-1][0])

    def test_bad_argument_has_no_side_effect(self):
        harness = RuntimeHarness()
        self.assertFalse(harness.runtime.run(["state=not-real"]))
        self.assertEqual(harness.builtins, [])
        self.assertEqual(harness.window.events, [])


if __name__ == "__main__":
    unittest.main()
