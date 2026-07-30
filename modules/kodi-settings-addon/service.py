from __future__ import absolute_import, division, print_function

import json
import threading
import time
from collections import deque

import xbmc
import xbmcaddon

from chapter_dialog import ChapterDialogManager
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


def log(message, level=xbmc.LOGINFO):
    xbmc.log("HTPC settings: %s" % message, level)


def set_setting(setting, value):
    response = json.loads(
        xbmc.executeJSONRPC(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "Settings.SetSettingValue",
                    "params": {"setting": setting, "value": value},
                    "id": setting,
                }
            )
        )
    )
    if "error" in response:
        log(
            "failed to set %s: %s" % (setting, response["error"]),
            xbmc.LOGERROR,
        )


def set_skin_setting(setting, enabled):
    command = "Skin.SetBool" if enabled else "Skin.Reset"
    xbmc.executebuiltin("%s(%s)" % (command, setting))


class ManagedSettings(object):
    """Apply core settings promptly and retry optional skin settings lazily."""

    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self.core_applied = False
        self.skin_applied = False
        self.next_skin_check = 0.0

    def tick(self):
        if not self.core_applied:
            self._apply_core()
            self.core_applied = True

        now = self.clock()
        if self.skin_applied or now < self.next_skin_check:
            return
        self.next_skin_check = now + 1.0
        if xbmc.getSkinDir() != "skin.bingie":
            return
        self._apply_bingie()
        self.skin_applied = True

    @staticmethod
    def _apply_core():
        set_setting("videoplayer.useprimedecoder", True)
        set_setting("videoplayer.useprimerenderer", 0)
        set_setting("videoplayer.adjustrefreshrate", 2)
        set_setting("videoscreen.whitelist", PLAYBACK_MODES)
        set_setting("videoscreen.whitelistpulldown", False)
        set_setting("videoscreen.whitelistdoublerefreshrate", False)
        set_setting("videoplayer.seeksteps", [-10, 10])
        set_setting("videoplayer.seekdelay", 0)
        set_setting("filelists.showparentdiritems", False)
        set_setting("input.enablemouse", False)
        set_setting("debug.showloginfo", False)

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
    def __init__(self):
        super(ServiceMonitor, self).__init__()
        self.events = deque()
        self.event_lock = threading.Lock()

    def _append(self, event):
        with self.event_lock:
            self.events.append(event)

    def post_input(self, action, payload=None):
        self._append(("input", action, payload or {}, time.monotonic()))

    def post_player(self, kind, payload=None):
        self._append(("player", kind, payload or {}, time.monotonic()))

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
        )
        self.settings = ManagedSettings()

    def run(self):
        # Publish readiness before any optional skin wait or settings work.
        self.lease.refresh(force=True)
        log("input router ready; managed settings scheduled")
        while not self.monitor.waitForAbort(0.05):
            self.lease.refresh()
            for event_type, name, payload, timestamp in self.monitor.drain():
                try:
                    if event_type == "player":
                        # Register the controller's current operation watermark
                        # before its callback can reset/advance the transaction.
                        self.view.update(
                            self.controller.snapshot(),
                            self.player.snapshot(),
                            timestamp,
                        )
                        self.view.on_player_event(name, payload, timestamp)
                        self.controller.on_player_event(name, payload, timestamp)
                        if name in ("started", "stopped", "ended"):
                            self.chapters.close()
                            self.chapters.clear_properties()
                    else:
                        self.router.handle(name, timestamp, payload)
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
                self.router.tick()
                self.chapters.validate()
                self.chapters.sync_properties()
            except Exception as error:
                self._recover("controller/presenter tick failed: %s" % error)

            try:
                self.settings.tick()
            except Exception as error:
                log("managed settings retry failed: %s" % error, xbmc.LOGERROR)

    def _recover(self, message):
        log(message, xbmc.LOGERROR)
        try:
            self.controller.cancel()
        except Exception as error:
            log("controller recovery failed: %s" % error, xbmc.LOGERROR)

    def close(self):
        self.chapters.close()
        self.chapters.clear_properties()
        try:
            self.controller.shutdown()
        finally:
            self.view.reset()
            self.publisher.clear()
            self.lease.stop()


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
