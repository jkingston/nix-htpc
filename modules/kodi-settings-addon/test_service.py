from __future__ import absolute_import, division, print_function

import json
import sys
import types
import unittest
from unittest import mock


BUILTINS = []
CONDITIONS = {}
INFO_LABELS = {}
WINDOWS = {}


class FakeMonitorBase(object):
    pass


class FakeKodiPlayerBase(object):
    pass


class FakeWindow(object):
    def __init__(self):
        self.properties = {}
        self.controls = {}
        self.operations = []

    def setProperty(self, name, value):
        self.operations.append(("set", name, value))
        self.properties[name] = value

    def clearProperty(self, name):
        self.operations.append(("clear", name, ""))
        self.properties.pop(name, None)

    def getProperty(self, name):
        return self.properties.get(name, "")

class FakeWindowXMLDialog(object):
    def __init__(self, *args, **kwargs):
        self.shown = False
        self.closed = False

    def show(self):
        self.shown = True

    def close(self):
        self.closed = True


class FakeListItem(object):
    def __init__(self, label="", label2=""):
        self.label = label
        self.label2 = label2
        self.art = {}
        self.properties = {}

    def setArt(self, art):
        self.art.update(art)

    def setProperty(self, name, value):
        self.properties[name] = value


fake_xbmc = types.ModuleType("xbmc")
fake_xbmc.LOGDEBUG = 0
fake_xbmc.LOGINFO = 1
fake_xbmc.LOGWARNING = 2
fake_xbmc.LOGERROR = 3
fake_xbmc.Monitor = FakeMonitorBase
fake_xbmc.Player = FakeKodiPlayerBase
fake_xbmc.executebuiltin = lambda command: BUILTINS.append(command)
fake_xbmc.getCondVisibility = lambda condition: CONDITIONS.get(condition, False)
fake_xbmc.getInfoLabel = lambda label: INFO_LABELS.get(label, "")
fake_xbmc.getSkinDir = lambda: "skin.bingie"
fake_xbmc.executeJSONRPC = lambda _request: '{"jsonrpc":"2.0","result":true}'
fake_xbmc.log = lambda _message, _level=1: None

fake_xbmcgui = types.ModuleType("xbmcgui")
fake_xbmcgui.Window = lambda window_id: WINDOWS.setdefault(
    window_id,
    FakeWindow(),
)
fake_xbmcgui.WindowXMLDialog = FakeWindowXMLDialog
fake_xbmcgui.ListItem = FakeListItem

fake_xbmcaddon = types.ModuleType("xbmcaddon")
fake_xbmcaddon.Addon = lambda: types.SimpleNamespace(
    getAddonInfo=lambda _name: "/addon"
)

sys.modules.setdefault("xbmc", fake_xbmc)
sys.modules.setdefault("xbmcgui", fake_xbmcgui)
sys.modules.setdefault("xbmcaddon", fake_xbmcaddon)

from chapter_dialog import ChapterDialogManager
from input_router import InputRouter, KodiCommands
from media_contract import (
    CHAPTERS_AVAILABLE,
    CHAPTERS_MANIFEST,
    CHAPTERS_PLAYBACK,
    CHAPTERS_REVISION,
    CHAPTERS_TOKEN,
    CHAPTER_AVAILABLE,
    CHAPTER_OPEN,
    PREVIEW_FRAME,
    PREVIEW_GENERATION,
    PREVIEW_PATH,
    PREVIEW_PLAYBACK,
    PREVIEW_REVISION,
    PREVIEW_SAMPLE,
    PREVIEW_TARGET,
    PREVIEW_TOKEN,
    SERVICE_PROTOCOL,
    SERVICE_READY,
    chapter_contract_available,
    parse_chapter_payload,
    validated_preview,
)
from presenter import (
    BingiePresenter,
    KodiPropertyPublisher,
    ServiceLease,
)
from player_adapter import KodiPlayerAdapter
from seek_controller import SCRUB_ACTIVE, RESUME_PENDING, SeekController
from service import (
    ManagedSettings,
    ServiceMonitor,
    get_setting,
    json_rpc_response,
    set_setting,
)


class FakeController(object):
    def __init__(self):
        self.state = "idle"
        self.source = ""
        self.hidden = []
        self.timeline = []
        self.confirms = []
        self.cancels = []
        self.ends = 0
        self.targets = []
        self.chapter_begins = 0

    @property
    def active(self):
        return self.state != "idle"

    @property
    def manual(self):
        return self.state in (
            "pause-pending",
            "scrub-active",
            "cancel-wait-pause",
            "committing",
            "resume-pending",
        )

    @property
    def back_dismisses_osd(self):
        return self.state == "skip-settling"

    def hidden_step(self, direction, timestamp):
        self.hidden.append((direction, timestamp))
        self.state = "skip-active"
        self.source = "fullscreen"
        return True

    def timeline_step(self, direction, timestamp):
        self.timeline.append((direction, timestamp))
        self.state = "pause-pending"
        self.source = "timeline"
        return True

    def confirm(self, timestamp=None):
        self.confirms.append(timestamp)
        self.state = "committing"
        return True

    def cancel(self, timestamp=None):
        self.cancels.append(timestamp)
        self.state = "resume-pending" if self.manual else "skip-settling"
        return True

    def end_optimistic_skip(self, _timestamp=None):
        self.ends += 1
        if self.active:
            self.state = "skip-settling"
        return True

    def begin_chapter_browse(self):
        self.chapter_begins += 1
        self.state = "pause-pending"
        self.source = "chapter"
        return True

    def set_target(self, seconds):
        self.targets.append(float(seconds))
        return True


class FakePresenter(object):
    def __init__(self):
        self.calls = []
        self.osd = False

    def emphasize_timeline(self):
        self.calls.append("emphasize")

    def focus_timeline(self):
        self.calls.append("timeline")

    def focus_transport(self):
        self.calls.append("transport")

    def focus_top_bar(self):
        self.calls.append("top")

    def show_osd(self):
        self.osd = True
        self.calls.append("show")

    def close_osd(self):
        self.osd = False
        self.calls.append("close")

    def osd_active(self):
        return self.osd


class FakeProvider(object):
    def __init__(self):
        self.token = "playback-one"
        self.chapters = [
            {
                "index": 0,
                "start_seconds": 0.0,
                "playback_token": self.token,
            },
            {
                "index": 1,
                "start_seconds": 600.0,
                "playback_token": self.token,
            },
        ]

    def load(self):
        return self.token, list(self.chapters)

    def available(self):
        return len(self.chapters) >= 2


class FakeChapters(object):
    def __init__(self):
        self.is_open = False
        self.is_available = False
        self.open_calls = []
        self.close_calls = 0
        self.provider = FakeProvider()

    def available(self):
        return self.is_available

    def open(self, current):
        self.open_calls.append(current)
        self.is_open = True
        return True

    def close(self):
        self.close_calls += 1
        self.is_open = False


class FakePlayer(object):
    def snapshot(self):
        return {"current": 123.0}


class JsonRpcResponseTest(unittest.TestCase):
    def test_set_setting_returns_success_and_sends_expected_request(self):
        response = json.dumps(
            {"jsonrpc": "2.0", "id": "example.setting", "result": True}
        )
        with mock.patch(
            "service.xbmc.executeJSONRPC",
            return_value=response,
        ) as rpc:
            self.assertTrue(set_setting("example.setting", "value"))

        request = json.loads(rpc.call_args.args[0])
        self.assertEqual(request["method"], "Settings.SetSettingValue")
        self.assertEqual(
            request["params"],
            {"setting": "example.setting", "value": "value"},
        )
        self.assertEqual(request["id"], "example.setting")

    def test_invalid_responses_fail_safely(self):
        failures = [
            ("exception", RuntimeError("unavailable")),
            ("malformed", "{"),
            ("non-object", "[]"),
            (
                "wrong-id",
                json.dumps(
                    {"jsonrpc": "2.0", "id": "other", "result": True}
                ),
            ),
            (
                "error",
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "request",
                        "error": {"code": -1, "message": "failed"},
                    }
                ),
            ),
            (
                "missing-result",
                json.dumps({"jsonrpc": "2.0", "id": "request"}),
            ),
        ]
        for name, response in failures:
            with self.subTest(name=name), mock.patch(
                "service.xbmc.executeJSONRPC",
                side_effect=response
                if isinstance(response, Exception)
                else None,
                return_value=None
                if isinstance(response, Exception)
                else response,
            ):
                self.assertIsNone(
                    json_rpc_response("Test.Method", {}, "request")
                )


class ManagedCoreSettingsTest(unittest.TestCase):
    def test_failed_write_returns_false_but_attempts_the_full_batch(self):
        outcomes = [False] + [True] * 10
        with mock.patch(
            "service.set_setting",
            side_effect=outcomes,
        ) as set_core:
            self.assertFalse(ManagedSettings._apply_core())

        self.assertEqual(set_core.call_count, 11)
        self.assertEqual(
            set_core.call_args_list[0],
            mock.call("videoplayer.useprimedecoder", True),
        )
        self.assertIn(
            mock.call("filelists.showparentdiritems", False),
            set_core.call_args_list,
        )
        self.assertEqual(
            set_core.call_args_list[-1],
            mock.call("debug.showloginfo", False),
        )

    def test_retry_deadlines_and_backoff_cap(self):
        now = [0.0]
        settings = ManagedSettings(clock=lambda: now[0])
        settings.skin_applied = True
        settings.screenshot_ready = True
        with mock.patch.object(
            ManagedSettings,
            "_apply_core",
            return_value=False,
        ) as apply_core, mock.patch("service.log") as log:
            for attempt, delay in enumerate(
                [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0],
                1,
            ):
                settings.tick()
                self.assertEqual(apply_core.call_count, attempt)

                deadline = now[0] + delay
                self.assertEqual(settings.next_core_check, deadline)

                now[0] = deadline - 0.001
                settings.tick()
                self.assertEqual(apply_core.call_count, attempt)
                now[0] = deadline

        self.assertFalse(settings.core_applied)
        self.assertEqual(settings.core_retry_delay, 30.0)
        log.assert_called_once_with(
            "managed core settings incomplete; retrying",
            fake_xbmc.LOGWARNING,
        )

    def test_eventual_success_stops_attempts(self):
        now = [0.0]
        settings = ManagedSettings(clock=lambda: now[0])
        settings.skin_applied = True
        settings.screenshot_ready = True
        with mock.patch.object(
            ManagedSettings,
            "_apply_core",
            side_effect=[False, True],
        ) as apply_core, mock.patch("service.log") as log:
            settings.tick()
            now[0] = settings.next_core_check - 0.001
            settings.tick()
            now[0] = settings.next_core_check
            settings.tick()
            self.assertTrue(settings.core_applied)
            now[0] += 100.0
            settings.tick()

        self.assertEqual(apply_core.call_count, 2)
        self.assertEqual(
            log.call_args_list,
            [
                mock.call(
                    "managed core settings incomplete; retrying",
                    fake_xbmc.LOGWARNING,
                ),
                mock.call("managed core settings ready"),
            ],
        )

    def test_core_failure_does_not_suppress_screenshot_or_skin(self):
        screenshot_path = "/fixture/core-independent"
        settings = ManagedSettings(
            clock=lambda: 0.0,
            screenshot_path=screenshot_path,
        )
        with mock.patch.object(
            ManagedSettings,
            "_apply_core",
            return_value=False,
        ) as apply_core, mock.patch.object(
            ManagedSettings,
            "_apply_bingie",
        ) as apply_skin, mock.patch(
            "service.get_setting",
            return_value=(True, screenshot_path),
        ) as get_screenshot:
            settings.tick()
            settings.tick()

        self.assertFalse(settings.core_applied)
        self.assertTrue(settings.screenshot_ready)
        self.assertTrue(settings.skin_applied)
        apply_core.assert_called_once_with()
        get_screenshot.assert_called_once_with("debug.screenshotpath")
        apply_skin.assert_called_once_with()


class ManagedScreenshotSettingsTest(unittest.TestCase):
    SCREENSHOT_PATH = "/fixture/managed-screenshots"

    def setUp(self):
        self.now = [0.0]
        self.settings = ManagedSettings(
            clock=lambda: self.now[0],
            screenshot_path=self.SCREENSHOT_PATH,
        )
        self.settings.core_applied = True
        self.settings.skin_applied = True

    @staticmethod
    def _response(request_id, result=None, error=None):
        response = {"jsonrpc": "2.0", "id": request_id}
        if error is None:
            response["result"] = result
        else:
            response["error"] = error
        return json.dumps(response)

    @classmethod
    def _get_response(cls, value):
        return cls._response(
            "get:debug.screenshotpath",
            {"value": value},
        )

    @classmethod
    def _set_response(cls, accepted):
        return cls._response("debug.screenshotpath", accepted)

    @staticmethod
    def _methods(rpc):
        return [
            json.loads(call.args[0])["method"]
            for call in rpc.call_args_list
        ]

    @staticmethod
    def _requests(rpc):
        return [
            json.loads(call.args[0])
            for call in rpc.call_args_list
        ]

    def test_invalid_get_result_shapes_are_unavailable(self):
        invalid_results = [None, True, [], {}, {"unexpected": "value"}]
        for result in invalid_results:
            with self.subTest(result=result), mock.patch(
                "service.xbmc.executeJSONRPC",
                return_value=self._response(
                    "get:debug.screenshotpath",
                    result,
                ),
            ):
                self.assertEqual(
                    get_setting("debug.screenshotpath"),
                    (False, None),
                )

    def test_retry_deadlines_and_backoff_cap(self):
        expected_delays = [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
        with mock.patch(
            "service.xbmc.executeJSONRPC",
            side_effect=RuntimeError("unavailable"),
        ) as rpc:
            for attempt, delay in enumerate(expected_delays, 1):
                self.settings.tick()
                self.assertEqual(rpc.call_count, attempt)

                deadline = self.now[0] + delay
                self.assertEqual(
                    self.settings.next_screenshot_check,
                    deadline,
                )

                self.now[0] = deadline - 0.001
                self.settings.tick()
                self.assertEqual(rpc.call_count, attempt)
                self.now[0] = deadline

        self.assertFalse(self.settings.screenshot_ready)
        self.assertEqual(self.settings.screenshot_retry_delay, 30.0)

    def test_rejected_write_warns_once_across_retries(self):
        with mock.patch(
            "service.xbmc.executeJSONRPC",
            side_effect=[
                self._get_response(""),
                self._set_response(False),
                self._get_response(""),
                self._set_response(False),
            ],
        ) as rpc, mock.patch("service.log") as log:
            self.settings.tick()
            self.now[0] = self.settings.next_screenshot_check
            self.settings.tick()

        self.assertFalse(self.settings.screenshot_ready)
        self.assertEqual(
            self._methods(rpc),
            [
                "Settings.GetSettingValue",
                "Settings.SetSettingValue",
            ] * 2,
        )
        log.assert_called_once_with(
            "managed screenshot path write was rejected",
            fake_xbmc.LOGWARNING,
        )

    def test_readback_mismatch_warns_once_and_uses_exact_requests(self):
        with mock.patch(
            "service.xbmc.executeJSONRPC",
            side_effect=[
                self._get_response(""),
                self._set_response(True),
                self._get_response("/fixture/not-managed"),
                self._get_response(""),
                self._set_response(True),
                self._get_response("/fixture/not-managed"),
            ],
        ) as rpc, mock.patch("service.log") as log:
            self.settings.tick()
            self.now[0] = self.settings.next_screenshot_check
            self.settings.tick()

        self.assertFalse(self.settings.screenshot_ready)
        expected_cycle = [
            {
                "jsonrpc": "2.0",
                "method": "Settings.GetSettingValue",
                "params": {"setting": "debug.screenshotpath"},
                "id": "get:debug.screenshotpath",
            },
            {
                "jsonrpc": "2.0",
                "method": "Settings.SetSettingValue",
                "params": {
                    "setting": "debug.screenshotpath",
                    "value": self.SCREENSHOT_PATH,
                },
                "id": "debug.screenshotpath",
            },
            {
                "jsonrpc": "2.0",
                "method": "Settings.GetSettingValue",
                "params": {"setting": "debug.screenshotpath"},
                "id": "get:debug.screenshotpath",
            },
        ]
        self.assertEqual(self._requests(rpc), expected_cycle * 2)
        log.assert_called_once_with(
            "managed screenshot path read-back did not match",
            fake_xbmc.LOGWARNING,
        )

    def test_eventual_success_stops_queries_and_writes(self):
        responses = [
            self._response(
                "get:debug.screenshotpath",
                error={"code": -1, "message": "not ready"},
            ),
            self._get_response(""),
            self._set_response(True),
            self._get_response(self.SCREENSHOT_PATH),
        ]
        with mock.patch(
            "service.xbmc.executeJSONRPC",
            side_effect=responses,
        ) as rpc:
            self.settings.tick()
            self.now[0] = self.settings.next_screenshot_check
            self.settings.tick()
            self.now[0] += 100.0
            self.settings.tick()

        self.assertTrue(self.settings.screenshot_ready)
        self.assertEqual(
            self._methods(rpc).count("Settings.SetSettingValue"),
            1,
        )
        self.assertEqual(rpc.call_count, 4)

    def test_matching_path_converges_without_a_write(self):
        with mock.patch(
            "service.xbmc.executeJSONRPC",
            return_value=self._get_response(self.SCREENSHOT_PATH),
        ) as rpc:
            self.settings.tick()
            self.now[0] = 100.0
            self.settings.tick()

        self.assertTrue(self.settings.screenshot_ready)
        self.assertEqual(
            self._methods(rpc),
            ["Settings.GetSettingValue"],
        )

    def test_screenshot_failure_does_not_rerun_core_or_suppress_skin(self):
        settings = ManagedSettings(
            clock=lambda: self.now[0],
            screenshot_path=self.SCREENSHOT_PATH,
        )
        with mock.patch.object(
            ManagedSettings,
            "_apply_core",
            return_value=True,
        ) as apply_core, mock.patch.object(
            ManagedSettings,
            "_apply_bingie",
        ) as apply_skin, mock.patch(
            "service.xbmc.executeJSONRPC",
            side_effect=RuntimeError("unavailable"),
        ) as rpc, mock.patch("service.log") as log:
            settings.tick()
            self.assertTrue(settings.core_applied)
            self.assertTrue(settings.skin_applied)
            self.assertFalse(settings.screenshot_ready)

            self.now[0] = settings.next_screenshot_check
            settings.tick()

        apply_core.assert_called_once_with()
        apply_skin.assert_called_once_with()
        self.assertEqual(rpc.call_count, 2)
        self.assertFalse(
            any(
                len(call.args) > 1
                and call.args[1] == fake_xbmc.LOGWARNING
                for call in log.call_args_list
            )
        )


class InputRouterTest(unittest.TestCase):
    def setUp(self):
        self.controller = FakeController()
        self.presenter = FakePresenter()
        self.chapters = FakeChapters()
        self.builtins = []
        self.router = InputRouter(
            self.controller,
            FakePlayer(),
            self.presenter,
            self.chapters,
            KodiCommands(self.builtins.append),
        )

    def test_hidden_arrows_start_optimistic_seek_and_open_timeline(self):
        self.assertTrue(self.router.handle("right", 1.0))
        self.assertEqual(self.controller.hidden, [(1, 1.0)])
        self.assertIn("emphasize", self.presenter.calls)

    def test_primary_during_any_modal_phase_commits_not_play_pause(self):
        for state in ("pause-pending", "scrub-active"):
            self.controller.state = state
            self.controller.source = "hold"
            self.router.handle("primary", 1.0 if state == "pause-pending" else 2.0)
            self.assertEqual(self.builtins, [])
            self.router.repeat_guard.reset()
        self.assertEqual(self.controller.confirms, [1.0, 2.0])

    def test_finished_commit_focuses_transport_not_timeline(self):
        self.controller.state = "scrub-active"
        self.controller.source = "timeline"
        self.router.handle("timeline-confirm", 1.0)
        self.assertNotIn("transport", self.presenter.calls)
        self.controller.state = "idle"
        self.router.tick()
        self.assertEqual(self.presenter.calls[-1], "transport")

    def test_scrub_is_modal_against_up_and_down(self):
        self.controller.state = "scrub-active"
        self.controller.source = "timeline"
        self.chapters.is_available = True
        self.router.handle("timeline-up", 1.0)
        self.router.handle("timeline-down", 1.1)
        self.assertEqual(self.controller.chapter_begins, 0)
        self.assertEqual(self.presenter.calls, [])
        self.assertEqual(self.builtins, [])

    def test_up_from_timeline_opens_pause_owned_chapter_rail(self):
        self.chapters.is_available = True
        self.router.handle("timeline-up", 1.0)
        self.assertEqual(self.controller.chapter_begins, 1)
        self.assertEqual(self.controller.source, "chapter")
        self.assertEqual(self.chapters.open_calls, [123.0])

    def test_chapter_focus_updates_target_and_select_commits(self):
        self.controller.state = "pause-pending"
        self.controller.source = "chapter"
        chapter = {
            "index": 1,
            "start_seconds": 600.0,
            "playback_token": "playback-one",
        }
        self.router.handle("chapter-focus", 1.0, chapter)
        self.router.handle("chapter-select", 1.1, chapter)
        self.assertEqual(self.controller.targets, [600.0, 600.0])
        self.assertEqual(self.controller.confirms, [None])
        self.assertEqual(self.router.pending_focus, "transport")

    def test_chapter_selection_from_another_transaction_is_ignored(self):
        self.controller.state = "scrub-active"
        self.controller.source = "timeline"
        self.router.handle(
            "chapter-select",
            1.0,
            {
                "index": 1,
                "start_seconds": 600,
                "playback_token": "playback-one",
            },
        )
        self.assertEqual(self.controller.targets, [])
        self.assertEqual(self.controller.confirms, [])

    def test_stale_chapter_selection_cancels_and_restores_timeline(self):
        self.controller.state = "scrub-active"
        self.controller.source = "chapter"
        self.router.handle(
            "chapter-select",
            1.0,
            {
                "index": 1,
                "start_seconds": 600,
                "playback_token": "stale-token",
            },
        )
        self.assertEqual(len(self.controller.cancels), 1)
        self.assertEqual(self.router.pending_focus, "timeline")
        self.controller.state = "idle"
        self.router.tick()
        self.assertEqual(self.presenter.calls[-1], "timeline")

    def test_chapter_up_preserves_top_destination_until_resume_finishes(self):
        self.controller.state = "scrub-active"
        self.controller.source = "chapter"
        self.router.handle(
            "chapter-exit",
            1.0,
            {"destination": "top"},
        )
        self.assertEqual(self.router.pending_focus, "top")
        self.assertNotIn("top", self.presenter.calls)
        self.controller.state = "idle"
        self.router.tick()
        self.assertEqual(self.presenter.calls[-1], "top")

    def test_chapter_down_and_back_restore_timeline(self):
        for destination in ("timeline", "back"):
            self.controller.state = "scrub-active"
            self.controller.source = "chapter"
            self.router.handle(
                "chapter-exit",
                1.0,
                {"destination": destination},
            )
            self.assertEqual(self.router.pending_focus, "timeline")
            self.controller.state = "idle"
            self.router.tick()
            self.assertEqual(self.presenter.calls[-1], "timeline")

    def test_chapter_select_arms_guard_across_dialog_to_osd_boundary(self):
        self.controller.state = "scrub-active"
        self.controller.source = "chapter"
        chapter = {
            "index": 1,
            "start_seconds": 600.0,
            "playback_token": "playback-one",
        }
        self.router.handle("chapter-select", 1.0, chapter)
        self.controller.state = "idle"
        self.router.tick()
        self.router.handle("osd-primary", 1.1)
        self.assertNotIn("Action(Select,videoosd)", self.builtins)
        self.router.handle("osd-primary", 1.61)
        self.assertIn("Action(Select,videoosd)", self.builtins)

    def test_physical_chapter_back_arms_guard_after_resume(self):
        self.controller.state = "scrub-active"
        self.controller.source = "chapter"
        self.presenter.osd = True
        self.router.handle(
            "chapter-exit",
            1.0,
            {"destination": "back", "arm_back": True},
        )
        self.controller.state = "idle"
        self.router.tick()
        self.router.handle("osd-back", 1.1)
        self.assertNotIn("close", self.presenter.calls)
        self.router.handle("osd-back", 1.61)
        self.assertIn("close", self.presenter.calls)

    def test_synthetic_chapter_exit_does_not_suppress_unrelated_back(self):
        self.controller.state = "scrub-active"
        self.controller.source = "chapter"
        self.presenter.osd = True
        self.router.handle(
            "chapter-exit",
            1.0,
            {"destination": "back"},
        )
        self.controller.state = "idle"
        self.router.tick()
        self.router.handle("osd-back", 1.1)
        self.assertIn("close", self.presenter.calls)

    def test_back_precedence_and_repeat_guard_prevent_cascade(self):
        self.chapters.is_open = True
        self.controller.state = "scrub-active"
        self.controller.source = "chapter"
        self.router.handle("fullscreen-back", 0.0)
        self.router.handle("fullscreen-back", 0.1)
        self.assertEqual(self.chapters.close_calls, 1)
        self.assertEqual(len(self.controller.cancels), 1)
        self.assertNotIn("PlayerControl(Stop)", self.builtins)

        self.controller.state = "idle"
        self.presenter.osd = False
        self.router.handle("fullscreen-back", 0.7)
        self.assertIn("PlayerControl(Stop)", self.builtins)

    def test_back_dismisses_osd_while_issued_skip_stays_attributed(self):
        for action in ("osd-back", "fullscreen-back"):
            with self.subTest(action=action):
                self.setUp()
                self.controller.state = "skip-settling"
                self.presenter.osd = True
                self.router.handle(action, 1.0)
                self.assertEqual(self.controller.cancels, [1.0])
                self.assertEqual(self.controller.state, "skip-settling")
                self.assertIn("close", self.presenter.calls)
                self.assertIsNone(self.router.pending_focus)
                self.assertEqual(self.builtins, [])


def chapter_properties():
    manifest = {
        "schema": 1,
        "playback": "playback-one",
        "revision": 4,
        "manifest_revision": 9,
        "expected_count": 2,
        "entries": [
            {
                "kind": "chapter",
                "index": 0,
                "time_seconds": 0,
                "label": "Opening",
                "image": "/tmp/chapter-0.jpg",
            },
            {
                "kind": "chapter",
                "index": 1,
                "time_seconds": 600,
                "label": "Next",
                "image": "/tmp/chapter-1.jpg",
            },
        ],
    }
    token = {
        "schema": 1,
        "playback": "playback-one",
        "revision": 4,
        "manifest_revision": 9,
    }
    return {
        CHAPTERS_AVAILABLE: "true",
        CHAPTERS_MANIFEST: json.dumps(manifest),
        CHAPTERS_TOKEN: json.dumps(token),
        CHAPTERS_PLAYBACK: "playback-one",
        CHAPTERS_REVISION: "4",
    }


class MediaContractTest(unittest.TestCase):
    def test_complete_chapter_contract_accepts_only_explicit_chapters(self):
        properties = chapter_properties()
        self.assertTrue(chapter_contract_available(properties))
        chapters = parse_chapter_payload(
            properties[CHAPTERS_MANIFEST],
            "playback-one",
        )
        self.assertEqual([item["start_seconds"] for item in chapters], [0, 600])

        payload = json.loads(properties[CHAPTERS_MANIFEST])
        payload["entries"][1]["kind"] = "bookmark"
        properties[CHAPTERS_MANIFEST] = json.dumps(payload)
        self.assertFalse(chapter_contract_available(properties))

    def test_mixed_chapter_revision_or_playback_is_rejected(self):
        for key, value in (
            (CHAPTERS_REVISION, "5"),
            (CHAPTERS_PLAYBACK, "playback-two"),
        ):
            properties = chapter_properties()
            properties[key] = value
            self.assertFalse(chapter_contract_available(properties))

    def test_partial_chapter_manifest_is_rejected(self):
        properties = chapter_properties()
        payload = json.loads(properties[CHAPTERS_MANIFEST])
        payload["expected_count"] = 3
        properties[CHAPTERS_MANIFEST] = json.dumps(payload)
        self.assertFalse(chapter_contract_available(properties))

    def test_chapter_parser_sorts_and_deduplicates_timestamps(self):
        properties = chapter_properties()
        payload = json.loads(properties[CHAPTERS_MANIFEST])
        payload["entries"].reverse()
        payload["entries"].append(
            {
                "kind": "chapter",
                "index": 3,
                "time_seconds": 600,
                "label": "Duplicate",
                "image": "/tmp/duplicate.jpg",
            }
        )
        chapters = parse_chapter_payload(json.dumps(payload), "playback-one")
        self.assertEqual([item["start_seconds"] for item in chapters], [0, 600])

    def test_preview_requires_atomic_token_and_all_matching_components(self):
        token = {
            "schema": 1,
            "playback": "playback-one",
            "seek_generation": "7",
            "target_seconds": 110,
            "sample_seconds": 100,
            "frame_index": 10,
            "revision": 4,
        }
        properties = {
            PREVIEW_PATH: "/tmp/frame-10.jpg",
            PREVIEW_TOKEN: json.dumps(token),
            PREVIEW_PLAYBACK: "playback-one",
            PREVIEW_GENERATION: "7",
            PREVIEW_TARGET: "110.0",
            PREVIEW_SAMPLE: "100.0",
            PREVIEW_FRAME: "10",
            PREVIEW_REVISION: "4",
        }
        snapshot = {
            "active": True,
            "generation": 7,
            "target_seconds": 110,
        }
        self.assertEqual(
            validated_preview(properties, snapshot),
            "/tmp/frame-10.jpg",
        )

        for key in (
            PREVIEW_TOKEN,
            PREVIEW_PLAYBACK,
            PREVIEW_GENERATION,
            PREVIEW_TARGET,
            PREVIEW_SAMPLE,
            PREVIEW_FRAME,
            PREVIEW_REVISION,
        ):
            incomplete = dict(properties)
            incomplete.pop(key)
            self.assertEqual(validated_preview(incomplete, snapshot), "")

    def test_preview_rejects_old_media_generation_and_target(self):
        properties = {
            PREVIEW_PATH: "/tmp/frame.jpg",
            PREVIEW_TOKEN: json.dumps(
                {
                    "schema": 1,
                    "playback": "old-playback",
                    "seek_generation": "6",
                    "target_seconds": 110,
                    "sample_seconds": 100,
                    "frame_index": 10,
                    "revision": 3,
                }
            ),
            PREVIEW_PLAYBACK: "old-playback",
            PREVIEW_GENERATION: "6",
            PREVIEW_TARGET: "110",
            PREVIEW_SAMPLE: "100",
            PREVIEW_FRAME: "10",
            PREVIEW_REVISION: "3",
        }
        self.assertEqual(
            validated_preview(
                properties,
                {"active": True, "generation": 7, "target_seconds": 110},
            ),
            "",
        )


class PresenterAndLeaseTest(unittest.TestCase):
    def setUp(self):
        BUILTINS[:] = []
        CONDITIONS.clear()
        WINDOWS.clear()

    def test_publisher_exposes_modal_only_for_transaction_snapshot(self):
        window = FakeWindow()
        publisher = KodiPropertyPublisher(window)
        snapshot = {
            "active": True,
            "generation": 1,
            "state": "pause-pending",
            "mode": "scrub",
            "source": "timeline",
            "target_seconds": 110,
            "percent": 10,
            "time": "1:50",
            "delta": "+0:10",
            "confirm": False,
            "modal": True,
            "controller_paused": False,
            "was_playing": True,
            "playback_epoch": 2,
            "hold": False,
            "hold_released": False,
        }
        publisher.publish(snapshot)
        self.assertEqual(window.getProperty("htpc.seek.modal"), "true")
        self.assertEqual(window.getProperty("htpc.seek.mode"), "scrub")
        self.assertEqual(window.getProperty("htpc.seek.percent"), "10.0000")
        self.assertEqual(window.getProperty("htpc.seek.previewbucket"), "2")

        snapshot["modal"] = False
        snapshot["percent"] = 100
        publisher.publish(snapshot)
        self.assertEqual(window.getProperty("htpc.seek.modal"), "")
        self.assertEqual(window.getProperty("htpc.seek.previewbucket"), "20")

    def test_view_publisher_commits_complete_inactive_slot_then_flips(self):
        window = FakeWindow()
        publisher = KodiPropertyPublisher(window)
        view = {
            "active": True,
            "target_revision": 1,
            "phase": "ready",
            "actual_percent": 10,
            "target_valid": True,
            "target_percent": 12.5,
            "time": "2:05",
            "delta": "+0:25",
            "prompt": "OK Seek",
            "preview_status": "ready",
            "preview_path": "/tmp/frame.jpg",
        }
        publisher.publish_view(view)
        self.assertEqual(window.getProperty("htpc.seek.viewslot"), "a")
        self.assertEqual(
            window.getProperty("htpc.seek.a.targetfill"),
            "0.0000,12.5000",
        )
        self.assertEqual(
            window.getProperty("htpc.seek.a.targetmarker"),
            "12.5000,12.5000",
        )
        self.assertEqual(
            window.getProperty("htpc.seek.a.previewanchor"),
            "13",
        )
        selector = next(
            index
            for index, operation in enumerate(window.operations)
            if operation[1] == "htpc.seek.viewslot"
        )
        active = next(
            index
            for index, operation in enumerate(window.operations)
            if operation[1] == "htpc.seek.viewactive"
        )
        slot_writes = [
            index
            for index, operation in enumerate(window.operations)
            if operation[1].startswith("htpc.seek.a.")
        ]
        self.assertTrue(slot_writes)
        self.assertLess(max(slot_writes), selector)
        self.assertLess(selector, active)

        window.operations[:] = []
        changed = dict(view)
        changed.update(
            target_revision=2,
            target_percent=67.5,
            time="11:15",
            delta="+9:35",
        )
        publisher.publish_view(changed)
        self.assertEqual(window.getProperty("htpc.seek.viewslot"), "b")
        self.assertEqual(
            window.getProperty("htpc.seek.b.targetfill"),
            "0.0000,67.5000",
        )
        self.assertEqual(
            window.getProperty("htpc.seek.b.previewanchor"),
            "68",
        )
        self.assertEqual(
            window.operations[-1],
            ("set", "htpc.seek.viewslot", "b"),
        )

        window.operations[:] = []
        changed_only_in_unrendered_actual = dict(changed)
        changed_only_in_unrendered_actual["actual_percent"] = 42
        publisher.publish_view(changed_only_in_unrendered_actual)
        self.assertEqual(window.operations, [])

    def test_view_publisher_clamps_bad_geometry_and_hides_first(self):
        window = FakeWindow()
        publisher = KodiPropertyPublisher(window)
        publisher.publish_view(
            {
                "active": True,
                "target_percent": float("inf"),
                "actual_percent": float("nan"),
                "target_valid": True,
            }
        )
        slot = window.getProperty("htpc.seek.viewslot")
        self.assertEqual(
            window.getProperty("htpc.seek.%s.targetfill" % slot),
            "0.0000,0.0000",
        )
        self.assertEqual(
            window.getProperty("htpc.seek.%s.targetmarker" % slot),
            "0.0000,0.0000",
        )
        self.assertEqual(
            window.getProperty("htpc.seek.%s.previewanchor" % slot),
            "0",
        )

        window.operations[:] = []
        publisher.publish_view({"active": False, "target_percent": 200})
        self.assertEqual(
            window.operations[0],
            ("clear", "htpc.seek.viewactive", ""),
        )
        self.assertEqual(window.getProperty("htpc.seek.viewactive"), "")
        window.operations[:] = []
        publisher.publish_view({"active": False, "target_percent": 200})
        self.assertEqual(window.operations, [])

    def test_controller_clear_cannot_erase_latched_view_slot(self):
        window = FakeWindow()
        publisher = KodiPropertyPublisher(window)
        publisher.publish(
            {
                "active": True,
                "generation": 1,
                "state": "skip-settling",
                "mode": "skip",
                "source": "timeline",
                "target_seconds": 110,
                "percent": 11,
                "time": "1:50",
                "delta": "+0:10",
                "confirm": False,
                "modal": False,
                "controller_paused": False,
                "was_playing": True,
                "playback_epoch": 1,
                "hold": False,
                "hold_released": False,
            }
        )
        publisher.publish_view(
            {
                "active": True,
                "target_revision": 1,
                "phase": "applying",
                "actual_percent": 10,
                "target_valid": True,
                "target_percent": 11,
                "preview_status": "ready",
                "preview_path": "/tmp/frame.jpg",
            }
        )
        slot = window.getProperty("htpc.seek.viewslot")
        publisher.clear_controller()
        self.assertEqual(window.getProperty("htpc.seek.active"), "")
        self.assertEqual(window.getProperty("htpc.seek.viewactive"), "true")
        self.assertEqual(window.getProperty("htpc.seek.viewslot"), slot)
        self.assertEqual(
            window.getProperty("htpc.seek.%s.previewpath" % slot),
            "/tmp/frame.jpg",
        )

    def test_lease_rearms_before_crash_expiry_and_clears_on_stop(self):
        self.assertEqual(SERVICE_READY, "htpc.service.ready")
        self.assertEqual(SERVICE_PROTOCOL, "htpc.service.protocol")
        now = [0.0]
        builtins = []
        window = FakeWindow()
        lease = ServiceLease(
            window=window,
            clock=lambda: now[0],
            builtin=builtins.append,
        )
        lease.refresh(force=True)
        self.assertEqual(window.getProperty(SERVICE_READY), "true")
        self.assertTrue(window.getProperty(SERVICE_PROTOCOL))
        self.assertIn("00:02", builtins[-1])
        count = len(builtins)
        now[0] = 0.74
        lease.refresh()
        self.assertEqual(len(builtins), count)
        now[0] = 0.75
        lease.refresh()
        self.assertGreater(len(builtins), count)
        lease.stop()
        self.assertEqual(window.getProperty(SERVICE_READY), "")

    def test_presenter_never_mutates_window_controls(self):
        CONDITIONS["Window.IsActive(videoosd)"] = True
        presenter = BingiePresenter()
        presenter.update({"active": True, "generation": 1, "percent": 25.0})
        self.assertNotIn(12901, WINDOWS)

    def test_bingie_settings_enable_information_bypass(self):
        calls = []
        with mock.patch("service.set_skin_setting", side_effect=lambda k, v: calls.append((k, v))):
            ManagedSettings._apply_bingie()
        self.assertIn(("ShowInformationBypass", True), calls)


class FakeDialog(object):
    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.shown = False
        self.closed = False
        self.__class__.instances.append(self)

    def show(self):
        self.shown = True

    def close_without_event(self):
        self.closed = True


class ChapterDialogManagerTest(unittest.TestCase):
    def setUp(self):
        FakeDialog.instances[:] = []
        self.window = FakeWindow()
        self.events = []
        self.provider = FakeProvider()
        self.manager = ChapterDialogManager(
            "/addon",
            lambda action, payload: self.events.append((action, payload)),
            provider=self.provider,
            dialog_class=FakeDialog,
            window=self.window,
        )

    def test_open_focus_select_and_close_publish_layer_state(self):
        self.assertTrue(self.manager.open(100))
        self.assertEqual(self.window.getProperty(CHAPTER_OPEN), "true")
        dialog = FakeDialog.instances[-1]
        dialog.kwargs["focus_callback"](dict(self.provider.chapters[1]))
        dialog.kwargs["select_callback"](dict(self.provider.chapters[1]))
        self.assertEqual(self.events[0][0], "chapter-focus")
        self.assertEqual(self.events[1][0], "chapter-select")
        self.assertEqual(
            self.events[1][1]["playback_token"],
            "playback-one",
        )
        self.assertEqual(self.window.getProperty(CHAPTER_OPEN), "")

    def test_sync_available_and_contract_loss_closes_dialog(self):
        self.manager.sync_properties()
        self.assertEqual(self.window.getProperty(CHAPTER_AVAILABLE), "true")
        self.manager.open()
        self.provider.chapters = []
        self.manager.sync_properties()
        self.assertEqual(self.window.getProperty(CHAPTER_AVAILABLE), "")
        self.assertTrue(FakeDialog.instances[-1].closed)
        self.assertEqual(
            self.events[-1],
            (
                "chapter-exit",
                {"destination": "back", "arm_back": False},
            ),
        )

    def test_revision_change_notifies_controller_cancel_path(self):
        self.manager.open()
        self.provider.token = "playback-two"
        self.manager.validate()
        self.assertTrue(FakeDialog.instances[-1].closed)
        self.assertEqual(
            self.events,
            [
                (
                    "chapter-exit",
                    {"destination": "back", "arm_back": False},
                )
            ],
        )


class ServiceMonitorTest(unittest.TestCase):
    def test_only_owned_notifications_enter_input_queue(self):
        monitor = ServiceMonitor()
        monitor.onNotification(
            "htpc.seek",
            "Other.timeline-left",
            '{"source":"skin"}',
        )
        monitor.onNotification("other.addon", "Other.timeline-right", "{}")
        events = monitor.drain()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1], "timeline-left")
        self.assertEqual(events[0][2], {"source": "skin"})


class AdapterDouble(KodiPlayerAdapter):
    def __init__(self, event_sink=None):
        self.playing = True
        self.current = 100.0
        self.duration = 3600.0
        self.pause_calls = 0
        self.seek_calls = []
        super(AdapterDouble, self).__init__(event_sink=event_sink)

    def isPlayingVideo(self):
        return self.playing

    def getTime(self):
        return self.current

    def getTotalTime(self):
        return self.duration

    def pause(self):
        self.pause_calls += 1
        CONDITIONS["Player.Paused"] = not CONDITIONS.get(
            "Player.Paused",
            False,
        )

    def seekTime(self, seconds):
        self.seek_calls.append(float(seconds))
        self.current = float(seconds)


class AdapterPublisher(object):
    def publish(self, _snapshot):
        pass

    def clear(self):
        pass


class PlayerAdapterAttributionTest(unittest.TestCase):
    def setUp(self):
        CONDITIONS.clear()
        INFO_LABELS.clear()
        CONDITIONS["Player.SeekEnabled"] = True
        INFO_LABELS["Player.Filenameandpath"] = "/media/movie.mkv"
        INFO_LABELS["VideoPlayer.DBID"] = ""
        INFO_LABELS["VideoPlayer.Title"] = ""
        self.events = []
        self.adapter = AdapterDouble(
            event_sink=lambda kind, payload: self.events.append(
                (kind, payload)
            )
        )
        self.adapter.epoch = 4

    def test_commands_revalidate_identity_and_epoch_before_mutation(self):
        snapshot = self.adapter.snapshot()
        INFO_LABELS["Player.Filenameandpath"] = "/media/other.mkv"
        self.assertFalse(
            self.adapter.request_pause(
                "pause-one",
                snapshot["identity"],
                snapshot["epoch"],
            )
        )
        self.assertFalse(
            self.adapter.request_seek(
                200,
                "seek-one",
                snapshot["identity"],
                snapshot["epoch"],
            )
        )
        self.assertEqual(self.adapter.pause_calls, 0)
        self.assertEqual(self.adapter.seek_calls, [])

        INFO_LABELS["Player.Filenameandpath"] = snapshot["identity"]
        CONDITIONS["Player.Paused"] = True
        self.assertFalse(
            self.adapter.request_resume(
                "resume-old-epoch",
                snapshot["identity"],
                snapshot["epoch"] - 1,
            )
        )
        self.assertEqual(self.adapter.pause_calls, 0)

    def test_idle_snapshot_never_probes_playback_only_player_fields(self):
        self.adapter.playing = False
        self.adapter.getTime = mock.Mock(
            side_effect=AssertionError("idle getTime probe")
        )
        self.adapter.getTotalTime = mock.Mock(
            side_effect=AssertionError("idle getTotalTime probe")
        )
        self.adapter.getPlayingFile = mock.Mock(
            side_effect=AssertionError("idle getPlayingFile probe")
        )

        snapshot = self.adapter.snapshot()
        self.assertFalse(snapshot["playing"])
        self.assertFalse(snapshot["seekable"])
        self.assertEqual(snapshot["current"], 0.0)
        self.assertEqual(snapshot["duration"], 0.0)
        self.assertEqual(snapshot["identity"], "")
        self.adapter.getTime.assert_not_called()
        self.adapter.getTotalTime.assert_not_called()
        self.adapter.getPlayingFile.assert_not_called()

    def test_mutable_dbid_and_title_do_not_change_identity(self):
        identity = self.adapter.snapshot()["identity"]
        INFO_LABELS["VideoPlayer.DBID"] = "42"
        INFO_LABELS["VideoPlayer.Title"] = "Populated later"
        self.assertEqual(self.adapter.snapshot()["identity"], identity)

    def test_retired_pause_callback_is_explicitly_untagged(self):
        self.assertTrue(
            self.adapter.request_pause(
                "pause-one",
                "/media/movie.mkv",
                4,
            )
        )
        self.adapter.retire_operation("pause-one")
        self.adapter.onPlayBackPaused()
        self.assertEqual(self.events[-1][0], "paused")
        self.assertIsNone(self.events[-1][1]["operation"])

    def test_seek_callbacks_match_target_out_of_order(self):
        self.adapter.request_seek(
            120,
            "seek-one",
            "/media/movie.mkv",
            4,
        )
        self.adapter.request_seek(
            240,
            "seek-two",
            "/media/movie.mkv",
            4,
        )
        self.adapter.onPlayBackSeek(240000, 0)
        self.adapter.onPlayBackSeek(120000, 0)
        self.assertEqual(
            [event[1]["operation"] for event in self.events],
            ["seek-two", "seek-one"],
        )

    def test_external_seek_does_not_consume_pending_intent(self):
        self.adapter.request_seek(
            120,
            "seek-one",
            "/media/movie.mkv",
            4,
        )
        self.adapter.onPlayBackSeek(500000, 0)
        self.assertIsNone(self.events[-1][1]["operation"])
        self.assertEqual(len(self.adapter.pending_seeks), 1)
        self.adapter.onPlayBackSeek(120500, 0)
        self.assertEqual(self.events[-1][1]["operation"], "seek-one")

    def test_dbid_title_population_does_not_strand_owned_pause(self):
        holder = {}
        self.adapter.event_sink = lambda kind, payload: holder[
            "controller"
        ].on_player_event(kind, payload, 0.1)
        controller = SeekController(self.adapter, AdapterPublisher())
        holder["controller"] = controller
        for timestamp in (0.0, 0.40, 0.508, 0.616):
            self.assertTrue(controller.timeline_step(1, timestamp))
        INFO_LABELS["VideoPlayer.DBID"] = "42"
        INFO_LABELS["VideoPlayer.Title"] = "Now populated"
        self.adapter.onPlayBackPaused()
        self.assertEqual(controller.state, SCRUB_ACTIVE)
        controller.cancel(0.2)
        self.assertEqual(controller.state, RESUME_PENDING)
        self.adapter.onPlayBackResumed()
        self.assertEqual(controller.state, "idle")

    def test_missing_pause_callback_is_unwound_after_timeout(self):
        holder = {}
        observed = []

        def sink(kind, payload):
            observed.append((kind, payload))
            holder["controller"].on_player_event(kind, payload, 1.0)

        self.adapter.event_sink = sink
        controller = SeekController(self.adapter, AdapterPublisher())
        holder["controller"] = controller
        for timestamp in (0.0, 0.40, 0.508, 0.616):
            controller.timeline_step(1, timestamp)
        self.assertTrue(CONDITIONS["Player.Paused"])
        controller.tick(1.366)
        self.assertEqual(controller.state, RESUME_PENDING)
        self.assertFalse(CONDITIONS["Player.Paused"])
        # The delayed pause callback cannot inherit the retired pause tag.
        self.adapter.onPlayBackPaused()
        self.assertIsNone(observed[-1][1]["operation"])
        self.assertEqual(controller.state, RESUME_PENDING)
        self.adapter.onPlayBackResumed()
        self.assertEqual(controller.state, "idle")


if __name__ == "__main__":
    unittest.main()
