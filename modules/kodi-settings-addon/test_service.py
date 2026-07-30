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

    def setProperty(self, name, value):
        self.properties[name] = value

    def clearProperty(self, name):
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
from service import ManagedSettings, ServiceMonitor


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
