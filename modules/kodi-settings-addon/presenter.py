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


LEASE_REFRESH_SECONDS = 0.75


class KodiPropertyPublisher(object):
    def __init__(self, window=None):
        self.window = window or xbmcgui.Window(HOME_WINDOW_ID)
        self.last = {}

    def publish(self, snapshot):
        percent = max(0.0, min(100.0, float(snapshot["percent"])))
        values = {
            "active": "true" if snapshot["active"] else "",
            "generation": str(snapshot["generation"]),
            "state": snapshot["state"],
            "mode": snapshot.get("mode", ""),
            "source": snapshot["source"],
            "targetseconds": str(snapshot["target_seconds"]),
            "percent": "%.4f" % percent,
            # Skin-owned 5% buckets let the preview follow the cursor without
            # retaining xbmcgui.Control pointers across OSD reconstruction.
            "previewbucket": str(int((percent + 2.5) // 5.0)),
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

    def update(self, snapshot):
        active = bool(snapshot["active"])
        osd_active = self.osd_active()

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
