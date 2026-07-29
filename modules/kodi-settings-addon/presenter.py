from __future__ import absolute_import, division, print_function

import time

import xbmc
import xbmcgui

from media_contract import (
    HOME_WINDOW_ID,
    SEEK_PREFIX,
    SEEK_PROPERTY_KEYS,
    SERVICE_PROTOCOL,
    SERVICE_PROTOCOL_VERSION,
    SERVICE_READY,
    PREVIEW_PATH,
    PREVIEW_TOKEN,
    PREVIEW_PLAYBACK,
    PREVIEW_GENERATION,
    PREVIEW_TARGET,
    PREVIEW_SAMPLE,
    PREVIEW_FRAME,
    PREVIEW_REVISION,
    validated_preview,
)


VIDEO_OSD_WINDOW_ID = 12901
TARGET_MARKER_CONTROL_ID = 1901
TARGET_CARD_CONTROL_ID = 1902
TIMELINE_X = 384
TIMELINE_WIDTH = 1152
TARGET_MARKER_WIDTH = 20
TARGET_MARKER_Y = 952
TARGET_CARD_WIDTH = 380
TARGET_CARD_Y = 650
LEASE_REFRESH_SECONDS = 0.75


class KodiPropertyPublisher(object):
    def __init__(self, window=None):
        self.window = window or xbmcgui.Window(HOME_WINDOW_ID)
        self.last = {}

    def publish(self, snapshot):
        values = {
            "active": "true" if snapshot["active"] else "",
            "generation": str(snapshot["generation"]),
            "state": snapshot["state"],
            "mode": snapshot.get("mode", ""),
            "source": snapshot["source"],
            "targetseconds": str(snapshot["target_seconds"]),
            "percent": "%.4f" % snapshot["percent"],
            "time": snapshot["time"],
            "delta": snapshot["delta"],
            "confirm": "true" if snapshot["confirm"] else "",
            "modal": "true" if snapshot.get("modal") else "",
            "controllerpaused": (
                "true" if snapshot.get("controller_paused") else ""
            ),
            "wasplaying": "true" if snapshot.get("was_playing") else "",
            "playbackepoch": str(snapshot.get("playback_epoch", "")),
            "hold": "true" if snapshot.get("hold") else "",
            "holdreleased": "true" if snapshot.get("hold_released") else "",
        }
        for key, value in values.items():
            if self.last.get(key) == value:
                continue
            if value:
                self.window.setProperty(SEEK_PREFIX + key, value)
            else:
                self.window.clearProperty(SEEK_PREFIX + key)
        self.last = values

    def refresh_preview(self, snapshot):
        keys = (
            PREVIEW_PATH,
            PREVIEW_TOKEN,
            PREVIEW_PLAYBACK,
            PREVIEW_GENERATION,
            PREVIEW_TARGET,
            PREVIEW_SAMPLE,
            PREVIEW_FRAME,
            PREVIEW_REVISION,
        )
        properties = dict(
            (key, self.window.getProperty(key))
            for key in keys
        )
        path = validated_preview(properties, snapshot)
        values = {
            "previewready": "true" if path else "",
            "previewpath": path,
        }
        for key, value in values.items():
            if self.last.get(key) == value:
                continue
            if value:
                self.window.setProperty(SEEK_PREFIX + key, value)
            else:
                self.window.clearProperty(SEEK_PREFIX + key)
            self.last[key] = value

    def clear(self):
        for key in SEEK_PROPERTY_KEYS:
            self.window.clearProperty(SEEK_PREFIX + key)
        self.last = {}


class ServiceLease(object):
    """A crash-expiring property used by conditional skin fallbacks."""

    def __init__(self, window=None, clock=None, builtin=None):
        self.window = window or xbmcgui.Window(HOME_WINDOW_ID)
        self.clock = clock or time.monotonic
        self.builtin = builtin or xbmc.executebuiltin
        self.next_refresh = 0.0

    def refresh(self, force=False):
        now = self.clock()
        if not force and now < self.next_refresh:
            return
        self.window.setProperty(SERVICE_READY, "true")
        self.window.setProperty(SERVICE_PROTOCOL, SERVICE_PROTOCOL_VERSION)
        self.builtin("CancelAlarm(HTPCServiceLease,silent)")
        self.builtin(
            "AlarmClock(HTPCServiceLease,"
            "ClearProperty(%s,Home),00:02,silent)" % SERVICE_READY
        )
        self.next_refresh = now + LEASE_REFRESH_SECONDS

    def stop(self):
        self.builtin("CancelAlarm(HTPCServiceLease,silent)")
        self.window.clearProperty(SERVICE_READY)
        self.window.clearProperty(SERVICE_PROTOCOL)
        self.next_refresh = 0.0


class BingiePresenter(object):
    def __init__(self, logger=None, clock=None):
        self.logger = logger
        self.clock = clock or time.monotonic
        self.last_generation = None
        self.last_active = False
        self.last_osd_active = False
        self.marker = None
        self.card = None
        self.last_marker_position = None
        self.last_card_position = None
        self.last_error_time = 0.0
        self.pending_timeline_focus = False

    @staticmethod
    def osd_active():
        return xbmc.getCondVisibility("Window.IsActive(videoosd)")

    def show_osd(self):
        if not self.osd_active():
            xbmc.executebuiltin("ActivateWindow(videoosd)")

    def emphasize_timeline(self):
        self.pending_timeline_focus = True
        self.show_osd()
        if self.osd_active():
            self.focus_timeline()
            self.pending_timeline_focus = False

    def close_osd(self):
        if self.osd_active():
            xbmc.executebuiltin("Dialog.Close(videoosd)")

    @staticmethod
    def focus_top_bar():
        xbmc.executebuiltin("SetFocus(300)")

    @staticmethod
    def focus_timeline():
        xbmc.executebuiltin("SetFocus(187)")

    @staticmethod
    def focus_transport():
        xbmc.executebuiltin("SetFocus(203)")

    @staticmethod
    def osd_action(action):
        xbmc.executebuiltin("Action(%s,videoosd)" % action)

    def _clear_control_cache(self):
        self.marker = None
        self.card = None
        self.last_marker_position = None
        self.last_card_position = None

    def update(self, snapshot):
        active = bool(snapshot["active"])
        osd_active = self.osd_active()

        if self.last_osd_active and not osd_active:
            self._clear_control_cache()
        self.last_osd_active = osd_active

        if not active:
            self.last_generation = None
            self.last_active = False
            return

        if snapshot["generation"] != self.last_generation:
            xbmc.executebuiltin("CancelAlarm(CloseVideoOSD,silent)")
            self.last_generation = snapshot["generation"]
        self.last_active = True

        if not osd_active:
            return
        if self.pending_timeline_focus:
            self.focus_timeline()
            self.pending_timeline_focus = False

        try:
            if self.marker is None or self.card is None:
                window = xbmcgui.Window(VIDEO_OSD_WINDOW_ID)
                self.marker = window.getControl(TARGET_MARKER_CONTROL_ID)
                self.card = window.getControl(TARGET_CARD_CONTROL_ID)

            cursor_x = TIMELINE_X + (
                TIMELINE_WIDTH * float(snapshot["percent"]) / 100.0
            )
            marker_position = (
                int(cursor_x - (TARGET_MARKER_WIDTH / 2.0)),
                TARGET_MARKER_Y,
            )
            card_position = (
                int(
                    max(
                        40,
                        min(
                            1920 - 40 - TARGET_CARD_WIDTH,
                            cursor_x - (TARGET_CARD_WIDTH / 2.0),
                        ),
                    )
                ),
                TARGET_CARD_Y,
            )
            if marker_position != self.last_marker_position:
                self.marker.setPosition(*marker_position)
                self.last_marker_position = marker_position
            if card_position != self.last_card_position:
                self.card.setPosition(*card_position)
                self.last_card_position = card_position
        except Exception as error:
            self._clear_control_cache()
            now = self.clock()
            if self.logger and now - self.last_error_time >= 5.0:
                self.logger(
                    "OSD preview update deferred: %s" % error,
                    xbmc.LOGWARNING,
                )
                self.last_error_time = now
