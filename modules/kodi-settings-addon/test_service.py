from __future__ import absolute_import, division, print_function

import sys
import types
import unittest


BUILTINS = []
CONDITIONS = {}


class FakeMonitorBase(object):
    pass


class FakeKodiPlayer(object):
    pass


fake_xbmc = types.ModuleType("xbmc")
fake_xbmc.LOGDEBUG = 0
fake_xbmc.LOGINFO = 1
fake_xbmc.LOGWARNING = 2
fake_xbmc.LOGERROR = 3
fake_xbmc.Monitor = FakeMonitorBase
fake_xbmc.Player = FakeKodiPlayer
fake_xbmc.executebuiltin = lambda command: BUILTINS.append(command)
fake_xbmc.getCondVisibility = lambda condition: CONDITIONS.get(condition, False)
fake_xbmc.getInfoLabel = lambda _label: ""
fake_xbmc.log = lambda _message, _level=1: None

fake_xbmcgui = types.ModuleType("xbmcgui")
fake_xbmcgui.Window = lambda _window_id: None

sys.modules.setdefault("xbmc", fake_xbmc)
sys.modules.setdefault("xbmcgui", fake_xbmcgui)

from seek_controller import RepeatGuard
from service import BingiePresenter, SeekService


class FakeController(object):
    def __init__(self):
        self.state = "idle"
        self.source = None
        self.arrow_calls = []
        self.commits = []
        self.confirms = []
        self.cancel_calls = 0
        self.reset_calls = 0

    @property
    def active(self):
        return self.state != "idle"

    @property
    def manual(self):
        return self.state in ("hold", "hold-pending", "timeline")

    def arrow(self, direction, source, timestamp):
        self.arrow_calls.append((direction, source, timestamp))
        self.state = "timeline" if source == "timeline" else "tap"
        self.source = source
        return True

    def commit(self, play_after, now):
        self.commits.append((play_after, now))
        self.state = "settling"
        self.source = "settling"
        return True

    def confirm(self, timestamp):
        self.confirms.append(timestamp)
        self.state = "settling"
        self.source = "settling"
        return True

    def cancel(self):
        self.cancel_calls += 1
        self.state = "idle"
        self.source = None
        return True

    def reset(self):
        self.reset_calls += 1
        self.state = "idle"
        self.source = None


class FakePresenter(object):
    def __init__(self):
        self.show_calls = 0
        self.close_calls = 0

    def show_osd(self):
        self.show_calls += 1

    def close_osd(self):
        self.close_calls += 1

    @staticmethod
    def osd_active():
        return CONDITIONS.get("Window.IsActive(videoosd)", False)


class SeekServiceInteractionTest(unittest.TestCase):
    def setUp(self):
        BUILTINS[:] = []
        CONDITIONS.clear()
        self.service = object.__new__(SeekService)
        self.service.controller = FakeController()
        self.service.presenter = FakePresenter()
        self.service.repeat_guard = RepeatGuard(quiet_period=0.50)
        self.service.passive_seek_mode = False
        self.service.passive_osd_seen = False

    def test_passive_osd_keeps_later_arrows_as_auto_commit_taps(self):
        self.service._handle("left", 0.0)
        self.assertTrue(self.service.passive_seek_mode)
        self.assertEqual(
            self.service.controller.arrow_calls[-1],
            (-1, "fullscreen", 0.0),
        )

        self.service._handle("osd-left", 0.8)
        self.assertEqual(
            self.service.controller.arrow_calls[-1],
            (-1, "fullscreen", 0.8),
        )

    def test_up_exits_passive_mode_and_explicit_timeline_is_manual(self):
        self.service._handle("right", 0.0)
        self.service._handle("osd-up", 0.2)
        self.assertFalse(self.service.passive_seek_mode)
        self.assertEqual(BUILTINS[-1], "Action(Up,videoosd)")

        focus = "Window.IsActive(videoosd) + Control.HasFocus(187)"
        CONDITIONS[focus] = True
        self.service._handle("osd-right", 0.3)
        self.assertEqual(
            self.service.controller.arrow_calls[-1],
            (1, "timeline", 0.3),
        )

    def test_delayed_osd_activation_does_not_clear_passive_seek(self):
        self.service._handle("right", 0.0)
        self.service._observe_passive_osd()
        self.assertTrue(self.service.passive_seek_mode)
        self.assertFalse(self.service.passive_osd_seen)

        CONDITIONS["Window.IsActive(videoosd)"] = True
        self.service._observe_passive_osd()
        self.assertTrue(self.service.passive_seek_mode)
        self.assertTrue(self.service.passive_osd_seen)

        CONDITIONS["Window.IsActive(videoosd)"] = False
        self.service._observe_passive_osd()
        self.assertFalse(self.service.passive_seek_mode)

    def test_ok_commits_implicit_tap_then_toggles_only_once(self):
        self.service._handle("right", 0.0)
        self.service._handle("osd-primary", 0.1)
        self.service._handle("osd-primary", 0.2)
        self.service._handle("osd-primary", 0.3)
        self.assertEqual(self.service.controller.commits, [(False, 0.1)])
        self.assertEqual(BUILTINS.count("PlayerControl(Play)"), 1)

    def test_ok_on_ready_timeline_toggles_playback_directly(self):
        focus = "Window.IsActive(videoosd) + Control.HasFocus(187)"
        CONDITIONS[focus] = True
        self.service._handle("osd-primary", 0.0)
        self.service._handle("osd-primary", 0.1)
        self.assertEqual(BUILTINS.count("PlayerControl(Play)"), 1)
        self.assertNotIn("Action(Select,videoosd)", BUILTINS)

    def test_held_back_cannot_cancel_close_and_then_stop(self):
        self.service.controller.state = "timeline"
        self.service.controller.source = "timeline"
        self.service._handle("osd-back", 0.0)
        self.service._handle("osd-back", 0.1)
        self.service._handle("fullscreen-back", 0.2)
        self.assertEqual(self.service.controller.cancel_calls, 1)
        self.assertEqual(self.service.presenter.close_calls, 0)
        self.assertNotIn("PlayerControl(Stop)", BUILTINS)

        self.service._handle("osd-back", 0.71)
        self.assertEqual(self.service.presenter.close_calls, 1)
        self.service._handle("fullscreen-back", 0.80)
        self.assertNotIn("PlayerControl(Stop)", BUILTINS)
        self.service._handle("fullscreen-back", 1.31)
        self.assertIn("PlayerControl(Stop)", BUILTINS)

    def test_lifecycle_notification_resets_pending_transaction(self):
        self.service.controller.state = "tap"
        self.service.passive_seek_mode = True
        self.service._handle("lifecycle-reset", 1.0)
        self.assertEqual(self.service.controller.reset_calls, 1)
        self.assertFalse(self.service.passive_seek_mode)

    def test_presenter_moves_skin_groups_with_base_control_api(self):
        class MovableControl(object):
            def __init__(self):
                self.positions = []

            def setPosition(self, x, y):
                self.positions.append((x, y))

        class OsdWindow(object):
            def __init__(self):
                self.controls = {1901: MovableControl(), 1902: MovableControl()}

            def getControl(self, control_id):
                return self.controls[control_id]

        osd_window = OsdWindow()
        original_window = fake_xbmcgui.Window
        fake_xbmcgui.Window = lambda _window_id: osd_window
        CONDITIONS["Window.IsActive(videoosd)"] = True
        try:
            presenter = BingiePresenter()
            presenter.update(
                {
                    "active": True,
                    "generation": 1,
                    "percent": 25.0,
                }
            )
        finally:
            fake_xbmcgui.Window = original_window

        self.assertEqual(osd_window.controls[1901].positions, [(656, 941)])
        self.assertEqual(osd_window.controls[1902].positions, [(482, 635)])


if __name__ == "__main__":
    unittest.main()
