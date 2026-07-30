from __future__ import absolute_import, division, print_function

import json
import threading
import time
from collections import deque

import xbmc
import xbmcaddon

from chapter_dialog import ChapterDialogManager
from input_quarantine import (
    DIRECTION_KEYS,
    INPUT_WATERMARK_PAYLOAD_KEY,
    canonical_physical_key,
)
from input_router import InputRouter, KodiCommands
from playback_view_model import PlaybackViewModel
from player_adapter import KodiPlayerAdapter
from presenter import BingiePresenter, KodiPropertyPublisher, ServiceLease
from seek_controller import SeekController


PLAYBACK_MODES = [
    "0384002160023.97603pstd",
    "0384002160024.00000pstd",
    "0384002160025.00000pstd",
    "0384002160029.97003pstd",
    "0384002160030.00000pstd",
]
SCREENSHOT_PATH = "@HTPC_SCREENSHOT_PATH@"
SCREENSHOT_SETTING = "debug.screenshotpath"
CORE_RETRY_INITIAL = 1.0
CORE_RETRY_MAX = 30.0
SCREENSHOT_RETRY_INITIAL = 1.0
SCREENSHOT_RETRY_MAX = 30.0
PLAYER_BOUNDARY_EVENTS = frozenset(("started", "stopped", "ended"))


def log(message, level=xbmc.LOGINFO):
    xbmc.log("HTPC settings: %s" % message, level)


def next_retry(now, delay, maximum):
    return now + delay, min(delay * 2.0, maximum)


def json_rpc_response(method, params, request_id):
    try:
        raw_response = xbmc.executeJSONRPC(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                    "id": request_id,
                }
            )
        )
        response = json.loads(raw_response)
    except Exception as error:
        log("%s failed: %s" % (method, error), xbmc.LOGERROR)
        return None

    if not isinstance(response, dict):
        log("%s returned a non-object response" % method, xbmc.LOGERROR)
        return None
    if response.get("id") != request_id:
        log("%s returned the wrong response id" % method, xbmc.LOGERROR)
        return None
    if "error" in response:
        log("%s failed: %s" % (method, response["error"]), xbmc.LOGERROR)
        return None
    if "result" not in response:
        log("%s returned no result" % method, xbmc.LOGERROR)
        return None
    return response


def set_setting(setting, value):
    status = set_setting_status(setting, value)
    return status is True


def set_setting_status(setting, value):
    response = json_rpc_response(
        "Settings.SetSettingValue",
        {"setting": setting, "value": value},
        setting,
    )
    if response is None:
        return None
    return response["result"] is True


def get_setting(setting):
    response = json_rpc_response(
        "Settings.GetSettingValue",
        {"setting": setting},
        "get:%s" % setting,
    )
    if response is None:
        return False, None

    result = response["result"]
    if not isinstance(result, dict) or "value" not in result:
        log(
            "Settings.GetSettingValue returned no value for %s" % setting,
            xbmc.LOGERROR,
        )
        return False, None
    return True, result["value"]


def set_skin_setting(setting, enabled):
    command = "Skin.SetBool" if enabled else "Skin.Reset"
    xbmc.executebuiltin("%s(%s)" % (command, setting))


class ManagedSettings(object):
    """Converge core, screenshot, and BINGIE settings independently."""

    def __init__(self, clock=None, screenshot_path=None):
        self.clock = time.monotonic if clock is None else clock
        self.core_applied = False
        self.next_core_check = 0.0
        self.core_retry_delay = CORE_RETRY_INITIAL
        self.core_warning_shown = False
        self.skin_applied = False
        self.next_skin_check = 0.0
        self.screenshot_path = (
            SCREENSHOT_PATH if screenshot_path is None else screenshot_path
        )
        self.screenshot_ready = False
        self.next_screenshot_check = 0.0
        self.screenshot_retry_delay = SCREENSHOT_RETRY_INITIAL
        self.screenshot_warnings = set()

    def tick(self):
        now = self.clock()
        self._tick_core(now)
        self._tick_screenshot(now)
        self._tick_skin(now)

    def _tick_core(self, now):
        if self.core_applied or now < self.next_core_check:
            return
        if self._apply_core():
            self.core_applied = True
            log("managed core settings ready")
            return
        if not self.core_warning_shown:
            self.core_warning_shown = True
            log(
                "managed core settings incomplete; retrying",
                xbmc.LOGWARNING,
            )
        self.next_core_check, self.core_retry_delay = next_retry(
            now,
            self.core_retry_delay,
            CORE_RETRY_MAX,
        )

    def _tick_screenshot(self, now):
        if self.screenshot_ready or now < self.next_screenshot_check:
            return

        available, current_path = get_setting(SCREENSHOT_SETTING)
        if not available:
            self._schedule_screenshot_retry(now)
            return

        if current_path != self.screenshot_path:
            write_status = set_setting_status(
                SCREENSHOT_SETTING,
                self.screenshot_path,
            )
            if write_status is not True:
                if write_status is False:
                    self._warn_screenshot_once(
                        "write-rejected",
                        "managed screenshot path write was rejected",
                    )
                self._schedule_screenshot_retry(now)
                return
            available, current_path = get_setting(SCREENSHOT_SETTING)
            if not available:
                self._schedule_screenshot_retry(now)
                return
            if current_path != self.screenshot_path:
                self._warn_screenshot_once(
                    "readback-mismatch",
                    "managed screenshot path read-back did not match",
                )
                self._schedule_screenshot_retry(now)
                return

        self.screenshot_ready = True
        log("managed screenshot path ready")

    def _warn_screenshot_once(self, failure, message):
        if failure in self.screenshot_warnings:
            return
        self.screenshot_warnings.add(failure)
        log(message, xbmc.LOGWARNING)

    def _schedule_screenshot_retry(self, now):
        (
            self.next_screenshot_check,
            self.screenshot_retry_delay,
        ) = next_retry(
            now,
            self.screenshot_retry_delay,
            SCREENSHOT_RETRY_MAX,
        )

    def _tick_skin(self, now):
        if self.skin_applied or now < self.next_skin_check:
            return
        self.next_skin_check = now + 1.0
        if xbmc.getSkinDir() != "skin.bingie":
            return
        self._apply_bingie()
        self.skin_applied = True

    @staticmethod
    def _apply_core():
        results = [
            set_setting("videoplayer.useprimedecoder", True),
            set_setting("videoplayer.useprimerenderer", 0),
            set_setting("videoplayer.adjustrefreshrate", 2),
            set_setting("videoscreen.whitelist", PLAYBACK_MODES),
            set_setting("videoscreen.whitelistpulldown", False),
            set_setting("videoscreen.whitelistdoublerefreshrate", False),
            set_setting("videoplayer.seeksteps", [-10, 10]),
            set_setting("videoplayer.seekdelay", 0),
            set_setting("filelists.showparentdiritems", False),
            set_setting("input.enablemouse", False),
            set_setting("debug.showloginfo", False),
        ]
        return all(results)

    @staticmethod
    def _apply_bingie():
        for setting in [
            "EnableAutoPauseOnOSD",
            "videoinfo_button_trakt",
            "videoinfo_button_plot",
            "videoinfo_button_versions",
            "videoinfo_button_favorites",
            "videoinfo_button_myrating",
            "videoinfo_button_refresh",
            "videoinfo_button_artwork",
            "videoinfo_button_wikipedia",
            "videoinfo_button_moreinfo",
            "videoinfo_button_trailersandmore",
        ]:
            set_skin_setting(setting, False)

        # Upstream's widget switch: ordinary Movie/TV/Episode rows call
        # Action(info), while the separately patched spotlight keeps Play and
        # More Info as explicit choices.
        set_skin_setting("ShowInformationBypass", True)


class ServiceMonitor(xbmc.Monitor):
    def __init__(self, clock=None):
        super(ServiceMonitor, self).__init__()
        self.clock = time.monotonic if clock is None else clock
        self.events = deque()
        self.event_lock = threading.Lock()
        self.dispatch_lock = threading.RLock()
        self.input_generation = 0
        self.input_last_seen = {}
        self.latest_direction = None
        self.latest_direction_seen = None

    def post_input(self, action, payload=None):
        with self.event_lock:
            timestamp = self.clock()
            physical_key = canonical_physical_key(action, payload)
            if physical_key is not None:
                self.input_last_seen[physical_key] = max(
                    timestamp,
                    self.input_last_seen.get(physical_key, timestamp),
                )
                if (
                    physical_key in DIRECTION_KEYS
                    and (
                        self.latest_direction_seen is None
                        or timestamp >= self.latest_direction_seen
                    )
                ):
                    self.latest_direction = physical_key
                    self.latest_direction_seen = timestamp
            self.events.append(
                (
                    "input",
                    action,
                    payload or {},
                    timestamp,
                    self.input_generation,
                )
            )

    def post_player(self, kind, payload=None):
        if kind not in PLAYER_BOUNDARY_EVENTS:
            with self.event_lock:
                self._append_player_locked(kind, payload)
            return

        with self.dispatch_lock:
            with self.event_lock:
                self.input_generation += 1
                current_generation = self.input_generation
                boundary_payload = dict(payload or {})
                boundary_payload[INPUT_WATERMARK_PAYLOAD_KEY] = (
                    self._input_watermark_locked()
                )
                self.events = deque(
                    event
                    for event in self.events
                    if (
                        event[0] != "input"
                        or event[4] >= current_generation
                    )
                )
                self._append_player_locked(kind, boundary_payload)

    def _input_watermark_locked(self):
        latest_direction = None
        if self.latest_direction is not None:
            latest_direction = {
                "key": self.latest_direction,
                "timestamp": self.latest_direction_seen,
            }
        return {
            "last_seen": dict(self.input_last_seen),
            "latest_direction": latest_direction,
        }

    def _append_player_locked(self, kind, payload):
        self.events.append(
            (
                "player",
                kind,
                payload or {},
                self.clock(),
                self.input_generation,
            )
        )

    def dispatch_input_if_current(self, generation, route):
        """Linearize generation validation and one input routing call."""
        with self.dispatch_lock:
            with self.event_lock:
                if generation != self.input_generation:
                    return False
            route()
            return True

    def current_input_generation(self):
        with self.event_lock:
            return self.input_generation

    def onNotification(self, sender, method, data):
        if sender not in ("htpc.seek", "htpc.chapter"):
            return
        action = method[len("Other.") :] if method.startswith("Other.") else method
        payload = {}
        if data:
            try:
                decoded = json.loads(data)
                if isinstance(decoded, dict):
                    payload = decoded
            except (TypeError, ValueError):
                pass
        self.post_input(action, payload)

    def drain(self):
        with self.event_lock:
            events = list(self.events)
            self.events.clear()
        return events


class SeekService(object):
    def __init__(self, monitor, addon_path=None):
        self.monitor = monitor
        self.publisher = KodiPropertyPublisher()
        self.presenter = BingiePresenter(logger=log)
        self.lease = ServiceLease()
        self.player = KodiPlayerAdapter(event_sink=monitor.post_player, logger=log)
        self.controller = SeekController(self.player, self.publisher)
        self.view = PlaybackViewModel()
        addon_path = addon_path or xbmcaddon.Addon().getAddonInfo("path")
        self.chapters = ChapterDialogManager(
            addon_path,
            event_sink=monitor.post_input,
        )
        self.router = InputRouter(
            self.controller,
            self.player,
            self.presenter,
            self.chapters,
            KodiCommands(xbmc.executebuiltin),
            clock=monitor.clock,
        )
        self.settings = ManagedSettings()

    def run(self):
        # Publish readiness before any optional skin wait or settings work.
        self.lease.refresh(force=True)
        log("input router ready; managed settings scheduled")
        while not self.monitor.waitForAbort(0.05):
            self.lease.refresh()
            for event in self.monitor.drain():
                event_type, name, payload, timestamp, _generation = event
                try:
                    self._dispatch_event(event)
                except Exception as error:
                    self._recover(
                        "%s event %s failed: %s"
                        % (event_type, name, error)
                    )

            try:
                self.controller.tick()
                snapshot = self.controller.snapshot()
                player_snapshot = self.player.snapshot()
                self.view.update(snapshot, player_snapshot)
                preview_path = self.publisher.refresh_preview(snapshot)
                self.view.offer_preview(
                    preview_path,
                    snapshot.get("generation"),
                    snapshot.get("target_seconds"),
                )
                self.publisher.publish_view(self.view.snapshot())
                self.presenter.update(snapshot)
                self._tick_router()
                self.chapters.validate()
                self.chapters.sync_properties()
            except Exception as error:
                self._recover("controller/presenter tick failed: %s" % error)

            try:
                self.settings.tick()
            except Exception as error:
                log("managed settings retry failed: %s" % error, xbmc.LOGERROR)

    def _dispatch_event(self, event):
        event_type, name, payload, timestamp, generation = event
        if event_type == "player":
            self._handle_player_event(name, payload, timestamp)
            return True
        return self.monitor.dispatch_input_if_current(
            generation,
            lambda: self.router.handle(
                name,
                timestamp,
                payload,
                input_generation=generation,
            ),
        )

    def _tick_router(self):
        """Deliver deferred focus only inside its originating media fence."""
        generation = self.router.pending_transition_generation
        if generation is None:
            self.router.tick()
            return True
        return self.monitor.dispatch_input_if_current(
            generation,
            self.router.tick,
        )

    def _handle_player_event(self, name, payload, timestamp):
        if name not in PLAYER_BOUNDARY_EVENTS:
            # Register the controller's current operation watermark before its
            # callback can reset/advance the transaction.
            self.view.update(
                self.controller.snapshot(),
                self.player.snapshot(),
                timestamp,
            )
            self.view.on_player_event(name, payload, timestamp)
            self.controller.on_player_event(name, payload, timestamp)
            return

        payload = dict(payload or {})
        input_watermark = payload.pop(
            INPUT_WATERMARK_PAYLOAD_KEY,
            None,
        )
        # Preserve callback ordering at a media boundary, while ensuring one
        # failing observer cannot prevent the remaining state from clearing.
        self._attempt(
            "player boundary watermark",
            lambda: self.view.update(
                self.controller.snapshot(),
                self.player.snapshot(),
                timestamp,
            ),
        )
        view_event_handled = self._attempt(
            "view player boundary",
            lambda: self.view.on_player_event(name, payload, timestamp),
        )
        controller_event_handled = self._attempt(
            "controller player boundary",
            lambda: self.controller.on_player_event(
                name,
                payload,
                timestamp,
            ),
        )
        if not view_event_handled:
            self._attempt("view boundary fallback reset", self.view.reset)
        if not controller_event_handled:
            self._attempt(
                "controller boundary fallback reset",
                lambda: self.controller.reset(clear_handoff=True),
            )
        self._attempt(
            "input router playback boundary",
            lambda: self.router.on_playback_boundary(
                timestamp,
                input_watermark,
            ),
        )
        self._attempt("presenter boundary reset", self.presenter.reset)
        self._attempt("chapter boundary close", self.chapters.close)
        self._attempt(
            "chapter boundary property clear",
            self.chapters.clear_properties,
        )
        self._attempt(
            "publisher boundary controller clear",
            self.publisher.clear_controller,
        )

    def _recover(self, message):
        log(message, xbmc.LOGERROR)
        self._attempt("input router recovery reset", self.router.reset)
        self._attempt("presenter recovery reset", self.presenter.reset)
        self._attempt("chapter recovery close", self.chapters.close)
        self._attempt(
            "chapter recovery property clear",
            self.chapters.clear_properties,
        )
        cancel_handled = self._attempt(
            "controller recovery cancel",
            self.controller.cancel,
        )
        if not cancel_handled:
            self._attempt(
                "controller recovery shutdown",
                self.controller.shutdown,
            )
        self._attempt("view recovery reset", self.view.reset)
        self._attempt("publisher recovery clear", self.publisher.clear)

    def close(self):
        self._attempt("input router shutdown clear", self.router.clear)
        self._attempt("presenter shutdown reset", self.presenter.reset)
        self._attempt("chapter shutdown close", self.chapters.close)
        self._attempt(
            "chapter shutdown property clear",
            self.chapters.clear_properties,
        )
        self._attempt("controller shutdown", self.controller.shutdown)
        self._attempt("view shutdown reset", self.view.reset)
        self._attempt("publisher shutdown clear", self.publisher.clear)
        self._attempt("service lease shutdown", self.lease.stop)

    @staticmethod
    def _attempt(label, action):
        try:
            action()
        except Exception as error:
            log("%s failed: %s" % (label, error), xbmc.LOGERROR)
            return False
        return True


def main():
    monitor = ServiceMonitor()
    service = SeekService(monitor)
    try:
        service.run()
    finally:
        service.close()
        log("seek service stopped")


if __name__ == "__main__":
    main()
