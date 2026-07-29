from __future__ import absolute_import, division, print_function

import json
import threading
import time
from collections import deque

import xbmc
import xbmcgui

from seek_controller import RepeatGuard, SeekController


PLAYBACK_MODES = [
    "0384002160023.97603pstd",
    "0384002160024.00000pstd",
    "0384002160025.00000pstd",
    "0384002160029.97003pstd",
    "0384002160030.00000pstd",
]

HOME_WINDOW_ID = 10000
VIDEO_OSD_WINDOW_ID = 12901
TIMELINE_CONTROL_ID = 187
TARGET_MARKER_CONTROL_ID = 1901
TARGET_CARD_CONTROL_ID = 1902
TIMELINE_X = 384
TIMELINE_WIDTH = 1152
TARGET_MARKER_WIDTH = 32
TARGET_MARKER_Y = 941
TARGET_CARD_WIDTH = 380
TARGET_CARD_Y = 635

PROPERTY_PREFIX = "htpc.seek."
PROPERTY_KEYS = (
    "active",
    "generation",
    "state",
    "source",
    "targetseconds",
    "percent",
    "time",
    "delta",
    "confirm",
)


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


def apply_managed_settings(monitor):
    set_setting("videoplayer.useprimedecoder", True)
    set_setting("videoplayer.useprimerenderer", 0)
    set_setting("videoplayer.adjustrefreshrate", 2)
    set_setting("videoscreen.whitelist", PLAYBACK_MODES)
    set_setting("videoscreen.whitelistpulldown", False)
    set_setting("videoscreen.whitelistdoublerefreshrate", False)
    # Native skip seeking remains a deterministic fallback for any unpatched
    # window. Fullscreen BINGIE playback is owned by SeekController.
    set_setting("videoplayer.seeksteps", [-10, 10])
    set_setting("videoplayer.seekdelay", 0)
    set_setting("filelists.showparentdiritems", False)
    set_setting("input.enablemouse", False)
    set_setting("debug.showloginfo", False)

    for _unused in range(30):
        if xbmc.getSkinDir() == "skin.bingie":
            break
        if monitor.waitForAbort(1):
            return

    if xbmc.getSkinDir() == "skin.bingie":
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


class KodiPlayerAdapter(object):
    def __init__(self):
        self.player = xbmc.Player()

    def is_seekable(self):
        return (
            self.player.isPlayingVideo()
            and xbmc.getCondVisibility("Player.SeekEnabled")
            and not xbmc.getCondVisibility("VideoPlayer.Content(livetv)")
            and not xbmc.getCondVisibility("VideoPlayer.HasMenu")
        )

    def get_time(self):
        return self.player.getTime()

    def get_duration(self):
        return self.player.getTotalTime()

    def is_paused(self):
        return xbmc.getCondVisibility("Player.Paused")

    def get_identity(self):
        return "|".join(
            (
                xbmc.getInfoLabel("Player.Filenameandpath"),
                xbmc.getInfoLabel("VideoPlayer.DBID"),
                xbmc.getInfoLabel("VideoPlayer.Title"),
            )
        )

    def seek(self, seconds):
        log("seek commit %.3f" % seconds, xbmc.LOGDEBUG)
        self.player.seekTime(float(seconds))

    def ensure_playing(self):
        if xbmc.getCondVisibility("Player.Paused"):
            self.player.pause()


class KodiPropertyPublisher(object):
    def __init__(self):
        self.window = xbmcgui.Window(HOME_WINDOW_ID)
        self.last = {}

    def publish(self, snapshot):
        values = {
            "active": "true" if snapshot["active"] else "",
            "generation": str(snapshot["generation"]),
            "state": snapshot["state"],
            "source": snapshot["source"],
            "targetseconds": str(snapshot["target_seconds"]),
            "percent": "%.4f" % snapshot["percent"],
            "time": snapshot["time"],
            "delta": snapshot["delta"],
            "confirm": "true" if snapshot["confirm"] else "",
        }
        for key, value in values.items():
            if self.last.get(key) == value:
                continue
            if value:
                self.window.setProperty(PROPERTY_PREFIX + key, value)
            else:
                self.window.clearProperty(PROPERTY_PREFIX + key)
        self.last = values

    def clear(self):
        for key in PROPERTY_KEYS:
            self.window.clearProperty(PROPERTY_PREFIX + key)
        self.last = {}


class BingiePresenter(object):
    def __init__(self):
        self.last_generation = None
        self.last_error_time = 0.0

    @staticmethod
    def osd_active():
        return xbmc.getCondVisibility("Window.IsActive(videoosd)")

    def show_osd(self):
        if not self.osd_active():
            # ActivateWindow is an idempotent open. The OSD action is a toggle,
            # so repeated CEC events could otherwise immediately close it.
            xbmc.executebuiltin("ActivateWindow(videoosd)")

    def close_osd(self):
        if self.osd_active():
            xbmc.executebuiltin("Dialog.Close(videoosd)")

    def update(self, snapshot):
        if not snapshot["active"]:
            self.last_generation = None
            return

        if snapshot["generation"] != self.last_generation:
            # An alarm armed before a seek began remains live even when the
            # auto-close helper becomes invisible. Cancel it transactionally.
            xbmc.executebuiltin("CancelAlarm(CloseVideoOSD,silent)")
            self.last_generation = snapshot["generation"]

        if not self.osd_active():
            return

        try:
            window = xbmcgui.Window(VIDEO_OSD_WINDOW_ID)
            cursor_x = TIMELINE_X + (
                TIMELINE_WIDTH * float(snapshot["percent"]) / 100.0
            )
            marker = window.getControl(TARGET_MARKER_CONTROL_ID)
            marker.setPosition(
                int(cursor_x - (TARGET_MARKER_WIDTH / 2.0)),
                TARGET_MARKER_Y,
            )

            card = window.getControl(TARGET_CARD_CONTROL_ID)
            card_x = int(
                max(
                    40,
                    min(
                        1920 - 40 - TARGET_CARD_WIDTH,
                        cursor_x - (TARGET_CARD_WIDTH / 2.0),
                    ),
                )
            )
            card.setPosition(card_x, TARGET_CARD_Y)
        except Exception as error:
            # The OSD may be between onload and control construction. The
            # 20 Hz service loop retries without blocking input delivery.
            now = time.monotonic()
            if now - self.last_error_time >= 5.0:
                log("OSD preview update deferred: %s" % error, xbmc.LOGWARNING)
                self.last_error_time = now


class SeekMonitor(xbmc.Monitor):
    def __init__(self):
        super(SeekMonitor, self).__init__()
        self.events = deque()
        self.event_lock = threading.Lock()

    def onNotification(self, sender, method, data):
        if method in ("Player.OnStop", "Player.OnAVChange", "Player.OnAVStart"):
            with self.event_lock:
                self.events.append(("lifecycle-reset", time.monotonic()))
            return
        if sender != "htpc.seek":
            return
        action = method[len("Other.") :] if method.startswith("Other.") else method
        with self.event_lock:
            self.events.append((action, time.monotonic()))

    def drain(self):
        with self.event_lock:
            events = list(self.events)
            self.events.clear()
        return events


class SeekService(object):
    def __init__(self, monitor):
        self.monitor = monitor
        self.player = KodiPlayerAdapter()
        self.publisher = KodiPropertyPublisher()
        self.presenter = BingiePresenter()
        self.controller = SeekController(self.player, self.publisher)
        self.repeat_guard = RepeatGuard()
        self.passive_seek_mode = False
        self.passive_osd_seen = False

    def run(self):
        log("managed settings applied; seek controller running")
        while not self.monitor.waitForAbort(0.05):
            for action, timestamp in self.monitor.drain():
                try:
                    self._handle(action, timestamp)
                except Exception as error:
                    self._recover("action %s failed: %s" % (action, error))
            try:
                self.controller.tick()
            except Exception as error:
                self._recover("controller tick failed: %s" % error)
            try:
                self.presenter.update(self.controller.snapshot())
            except Exception as error:
                # Presentation is optional; never sacrifice remote delivery.
                log("presenter update failed: %s" % error, xbmc.LOGERROR)
            self._observe_passive_osd()
        self.controller.reset()

    def _set_passive_seek_mode(self, enabled):
        enabled = bool(enabled)
        if enabled and not self.passive_seek_mode:
            self.passive_osd_seen = self.presenter.osd_active()
        elif not enabled:
            self.passive_osd_seen = False
        self.passive_seek_mode = enabled

    def _observe_passive_osd(self):
        if not self.passive_seek_mode:
            return
        if self.presenter.osd_active():
            self.passive_osd_seen = True
        elif self.passive_osd_seen:
            # Clear only after the asynchronously opened OSD was observed and
            # then closed, never in the same loop that requested activation.
            self._set_passive_seek_mode(False)

    def _recover(self, message):
        log(message, xbmc.LOGERROR)
        try:
            self.controller.reset()
        except Exception as error:
            log("controller reset failed: %s" % error, xbmc.LOGERROR)
        self._set_passive_seek_mode(False)

    @staticmethod
    def _timeline_focused():
        return xbmc.getCondVisibility(
            "Window.IsActive(videoosd) + Control.HasFocus(%d)"
            % TIMELINE_CONTROL_ID
        )

    @staticmethod
    def _osd_action(action):
        xbmc.executebuiltin("Action(%s,videoosd)" % action)

    def _primary(self, timestamp, from_osd):
        if not self.repeat_guard.accept("select", timestamp):
            return

        if self.controller.manual:
            self.controller.confirm(timestamp)
            self._set_passive_seek_mode(False)
            self.presenter.show_osd()
            return

        if self.controller.state == "tap":
            self.controller.commit(play_after=False, now=timestamp)
            xbmc.executebuiltin("PlayerControl(Play)")
            self._set_passive_seek_mode(False)
            self.presenter.show_osd()
            return

        if self.passive_seek_mode:
            # OK retains the standard play/pause meaning after an implicit
            # seek, even while its playhead is still settling.
            xbmc.executebuiltin("PlayerControl(Play)")
            self._set_passive_seek_mode(False)
            self.presenter.show_osd()
            return

        if from_osd and self._timeline_focused():
            # With no pending target, OK on the ready timeline retains its
            # normal play/pause meaning. Do this directly so the timeline's
            # NotifyAll onclick cannot bounce through the repeat guard.
            xbmc.executebuiltin("PlayerControl(Play)")
            return

        if from_osd:
            self._osd_action("Select")
        else:
            xbmc.executebuiltin("PlayerControl(Play)")
            self.presenter.show_osd()

    def _handle(self, action, timestamp):
        if action == "lifecycle-reset":
            self.controller.reset()
            self._set_passive_seek_mode(False)
            return

        if action in ("left", "right"):
            direction = -1 if action == "left" else 1
            if self.controller.arrow(direction, "fullscreen", timestamp):
                self._set_passive_seek_mode(True)
                self.presenter.show_osd()
            return

        if action in ("osd-left", "osd-right"):
            direction = -1 if action.endswith("left") else 1
            if self._timeline_focused():
                self._set_passive_seek_mode(False)
                self.controller.arrow(direction, "timeline", timestamp)
            elif self.passive_seek_mode:
                self.controller.arrow(direction, "fullscreen", timestamp)
            else:
                self._osd_action("Left" if direction < 0 else "Right")
            return

        if action in ("osd-up", "osd-down"):
            if self.controller.state == "tap":
                self.controller.commit(play_after=False, now=timestamp)
            self._set_passive_seek_mode(False)
            self._osd_action("Up" if action.endswith("up") else "Down")
            return

        if action in ("timeline-left", "timeline-right"):
            direction = -1 if action.endswith("left") else 1
            self._set_passive_seek_mode(False)
            self.controller.arrow(direction, "timeline", timestamp)
            return

        if action == "timeline-confirm":
            self._primary(timestamp, from_osd=True)
            return

        if action == "primary":
            self._primary(timestamp, from_osd=False)
            return

        if action == "osd-primary":
            self._primary(timestamp, from_osd=True)
            return

        if action == "osd-show":
            if self.controller.state == "tap":
                self.controller.commit(play_after=False, now=timestamp)
            self._set_passive_seek_mode(False)
            self.presenter.show_osd()
            return

        if action == "osd-back":
            if not self.repeat_guard.accept("back", timestamp):
                return
            source = self.controller.source
            if self.controller.manual:
                self.controller.cancel()
                self._set_passive_seek_mode(False)
                return
            if self.controller.active:
                self.controller.cancel()
                if source in ("fullscreen", "settling"):
                    self.presenter.close_osd()
                self._set_passive_seek_mode(False)
                return
            self.presenter.close_osd()
            self._set_passive_seek_mode(False)
            return

        if action == "fullscreen-back":
            if not self.repeat_guard.accept("back", timestamp):
                return
            if self.controller.active:
                self.controller.cancel()
            self._set_passive_seek_mode(False)
            xbmc.executebuiltin("PlayerControl(Stop)")
            return

        if action == "timeline-cancel" and self.controller.manual:
            self.controller.cancel()


def main():
    monitor = SeekMonitor()
    try:
        apply_managed_settings(monitor)
        if not monitor.abortRequested():
            SeekService(monitor).run()
    finally:
        KodiPropertyPublisher().clear()
        log("seek controller stopped")


if __name__ == "__main__":
    main()
