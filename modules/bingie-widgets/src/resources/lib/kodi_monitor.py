#!/usr/bin/python
# -*- coding: utf-8 -*-

import os, sys
import threading
from resources.lib.utils import log_msg
import xbmc
import time
import json
from resources.lib.episode_picker import (
    next_picker_position,
    season_anchor_positions,
)


class SeasonAnchorIndexer(object):
    """Build season anchors away from the skin's render and input paths."""

    def __init__(self, win):
        self.win = win
        self._lock = threading.RLock()
        self._cache = {}
        self._pending = set()
        self._focus_requests = {}
        self._jobs = []
        self._wake = threading.Event()
        self._stopped = False
        self._thread = threading.Thread(
            target=self._run,
            name="bingie-season-anchors",
        )
        self._thread.daemon = True
        self._thread.start()

    def request(self, show_id):
        show_id = self._normalise_id(show_id)
        if not show_id:
            return
        with self._lock:
            if show_id in self._cache or show_id in self._pending:
                return
            self._pending.add(show_id)
            self._jobs.append(show_id)
            self._wake.set()

    def focus(self, show_id, season):
        show_id = self._normalise_id(show_id)
        season = self._normalise_id(season)
        if not show_id or not season:
            return
        container = self._normalise_id(
            self.win.getProperty("BingieEpisodeAnchorContainer")
        )
        if container not in ("525", "5027"):
            container = "525"

        with self._lock:
            anchors = self._cache.get(show_id)
            if anchors is None:
                self._focus_requests[show_id] = (season, container)
                self.request(show_id)
                return
            position = anchors.get(season)

        if position:
            self._set_focus(position, container)
            self.win.clearProperty("BingieEpisodeAnchorSeason")

    def invalidate(self):
        with self._lock:
            self._cache.clear()

    def stop(self):
        with self._lock:
            self._stopped = True
            self._wake.set()
        self._thread.join(1.0)

    def _run(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                if self._stopped:
                    return
                show_id = self._jobs.pop(0) if self._jobs else ""
            if not show_id:
                continue

            anchors = self._load(show_id)
            with self._lock:
                self._pending.discard(show_id)
                if anchors is not None:
                    self._cache[show_id] = anchors
                focus_request = self._focus_requests.pop(show_id, None)
            if anchors is not None and self.win.getProperty("BingieEpisodeAnchorShowID") == show_id:
                self.win.setProperty("BingieEpisodeAnchorReady", show_id)
                if focus_request:
                    season, container = focus_request
                    position = anchors.get(season)
                    if position and self.win.getProperty("BingieEpisodeAnchorShowID") == show_id:
                        self._set_focus(position, container)
                        self.win.clearProperty("BingieEpisodeAnchorSeason")

    @staticmethod
    def _normalise_id(value):
        try:
            return str(int(value))
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _load(show_id):
        request = {
            "jsonrpc": "2.0",
            "method": "VideoLibrary.GetEpisodes",
            "params": {
                "tvshowid": int(show_id),
                "properties": ["season", "episode"],
            },
            "id": "bingie-season-anchors-%s" % show_id,
        }
        try:
            response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
            return season_anchor_positions(
                response.get("result", {}).get("episodes", [])
            )
        except Exception as exc:
            log_msg(
                "Season anchor query failed for show %s: %s" % (show_id, exc),
                xbmc.LOGWARNING,
            )
            return None

    @staticmethod
    def _set_focus(position, container):
        xbmc.executebuiltin(
            "SetFocus(%s,%s,absolute)" % (container, position)
        )


class KodiMonitor(xbmc.Monitor):
    ''' Monitor all events in Kodi '''
    update_widgets_busy = False
    last_mediatype = ""

    def __init__(self, **kwargs):
        xbmc.Monitor.__init__(self)
        self.win = kwargs.get("win")
        self.addon = kwargs.get("addon")
        self.season_anchors = SeasonAnchorIndexer(self.win)

    def tick(self):
        """Consume picker requests without running work on the UI path."""
        show_id = self.win.getProperty("BingieEpisodeAnchorShowID")
        season = self.win.getProperty("BingieEpisodeAnchorSeason")
        if show_id:
            self.season_anchors.request(show_id)
        if show_id and season:
            self.season_anchors.focus(show_id, season)

    def onNotification(self, sender, method, data):
        ''' builtin function for the xbmc.Monitor class '''
        try:
            log_msg("Kodi_Monitor: sender %s - method: %s  - data: %s" % (sender, method, data))
            data = json.loads(data)
            mediatype = ""
            if data and isinstance(data, dict):
                if data.get("item"):
                    mediatype = data["item"].get("type", "")
                elif data.get("type"):
                    mediatype = data["type"]

            if method in ("VideoLibrary.OnScanFinished", "VideoLibrary.OnCleanFinished"):
                # Episode play-count updates do not change season positions and
                # must not cause a large show's anchor map to be rebuilt.
                self.season_anchors.invalidate()

            if method == "VideoLibrary.OnUpdate":
                if not mediatype:
                    mediatype = self.last_mediatype # temp hack
                self.refresh_video_widgets(mediatype)

            if method == "Player.OnStop":
                self.last_mediatype = mediatype
                if mediatype == "episode":
                    self._advance_episode_picker()
                    # Episode progress drives Continue, Up Next and show play
                    # targets, so correctness cannot depend on an optional
                    # aggressive-refresh setting.
                    self.refresh_video_widgets(mediatype)
                elif (
                        mediatype == "movie"
                        and self.addon.getSetting("aggresive_refresh") == "true"):
                    self.refresh_video_widgets(mediatype)

        except Exception as exc:
            log_msg("Exception in KodiMonitor: %s" % exc, xbmc.LOGERROR)

    def stop(self):
        self.season_anchors.stop()

    def _advance_episode_picker(self):
        """Remember the next item when playback returns to a picker."""
        container = self.win.getProperty("BingieEpisodePickerContainer")
        if container not in ("525", "5027"):
            return
        position = next_picker_position(
            self.win.getProperty("BingieEpisodePickerPosition")
        )
        if not position:
            return
        # Update the source position as well so a chain of autoplayed episodes
        # advances once per stop rather than repeatedly targeting one item.
        self.win.setProperty("BingieEpisodePickerPosition", position)
        self.win.setProperty("BingieEpisodeFocus", position)
        self.win.setProperty("BingieEpisodeFocusContainer", container)

    def refresh_video_widgets(self, media_type):
        ''' refresh video widgets '''
        log_msg("Video database changed - type: %s - refreshing widgets...." % media_type)
        timestr = self._generation()
        self.win.setProperty("widgetreload", timestr)
        if media_type:
            property_type = {
                "movie": "movies",
                "episode": "episodes",
                "tvshow": "tvshows",
            }.get(media_type, media_type)
            self.win.setProperty("widgetreload-%s" % property_type, timestr)
            if "episode" in media_type:
                self.win.setProperty("widgetreload-tvshows", timestr)

    def onSettingsChanged(self):
        ''' called by Kodi when the addon settings are changed '''
        timestr = self._generation()
        self.win.setProperty("widgetreload", timestr)
        self.win.setProperty("widgetreload2", timestr)
        for media_type in ["episodes", "tvshows", "movies"]:
            self.win.setProperty("widgetreload-%s" % media_type, timestr)

    @staticmethod
    def _generation():
        """Return a token that remains unique for adjacent Kodi events."""
        return str(int(time.time() * 1000000))
