from __future__ import absolute_import, division, print_function

import json
import math
import threading
from collections import deque

import xbmc


SEEK_CALLBACK_TOLERANCE_SECONDS = 2.0
OSD_PAUSE_REQUEST_ID = "htpc.pause-for-osd"


class KodiPlayerAdapter(xbmc.Player):
    """Identity-bound player commands and attributed lifecycle callbacks.

    Kodi callbacks do not carry an application operation identifier. Pending
    intents are therefore retained only while the controller still expects
    them, bound to one playback identity/epoch, and seek callbacks are matched
    by their reported target rather than blindly consuming a FIFO entry.
    """

    def __init__(self, event_sink=None, logger=None, rpc=None):
        super(KodiPlayerAdapter, self).__init__()
        self.event_sink = event_sink
        self.logger = logger
        self.rpc = xbmc.executeJSONRPC if rpc is None else rpc
        self.epoch = 0
        self.pending_pause = None
        self.pending_resume = None
        self.pending_seeks = deque()
        self._lock = threading.RLock()

    def _identity(self):
        # DBID and title are populated lazily and may change during startup.
        # Filename/path is the stable item identity; epoch distinguishes
        # repeated playback of the same path.
        playing_file = ""
        try:
            if self.isPlayingVideo():
                playing_file = self.getPlayingFile()
        except Exception:
            pass
        return playing_file or xbmc.getInfoLabel("Player.Filenameandpath")

    def _snapshot_locked(self):
        playing = self.isPlayingVideo()
        seekable = (
            playing
            and xbmc.getCondVisibility("Player.SeekEnabled")
            and not xbmc.getCondVisibility("VideoPlayer.Content(livetv)")
            and not xbmc.getCondVisibility("VideoPlayer.HasMenu")
        )
        current = 0.0
        duration = 0.0
        identity = ""
        if playing:
            try:
                current = float(self.getTime())
            except Exception:
                current = 0.0
            try:
                duration = float(self.getTotalTime())
            except Exception:
                duration = 0.0
            identity = self._identity()
        return {
            "seekable": seekable,
            "playing": playing,
            "current": current,
            "duration": duration,
            "paused": xbmc.getCondVisibility("Player.Paused"),
            "identity": identity,
            "epoch": self.epoch,
        }

    def snapshot(self):
        with self._lock:
            return self._snapshot_locked()

    def video_active(self):
        """Report live video presence without exposing broader player state."""
        with self._lock:
            try:
                return bool(self.isPlayingVideo())
            except Exception as error:
                self._log_error("video activity probe failed: %s" % error)
                return False

    def pause_for_osd(self):
        """Idempotently request pause without joining a seek transaction."""
        request = {
            "jsonrpc": "2.0",
            "method": "Player.PlayPause",
            "params": {"playerid": 1, "play": False},
            "id": OSD_PAUSE_REQUEST_ID,
        }
        try:
            response = json.loads(self.rpc(json.dumps(request)))
        except Exception as error:
            self._log_error("OSD pause request failed: %s" % error)
            return False

        if (
            not isinstance(response, dict)
            or response.get("jsonrpc") != "2.0"
            or response.get("id") != OSD_PAUSE_REQUEST_ID
            or "error" in response
            or not isinstance(response.get("result"), dict)
        ):
            self._log_error("OSD pause request returned an invalid response")
            return False

        speed = response["result"].get("speed")
        valid_speed = (
            isinstance(speed, (int, float))
            and not isinstance(speed, bool)
            and speed == 0
        )
        if not valid_speed:
            self._log_error("OSD pause request did not confirm speed zero")
            return False
        return True

    def _log_error(self, message):
        if self.logger is None:
            return
        try:
            self.logger(message, xbmc.LOGERROR)
        except Exception:
            pass

    @staticmethod
    def _expected_matches(snapshot, identity, epoch):
        return (
            snapshot.get("identity") == identity
            and snapshot.get("epoch") == epoch
        )

    def request_pause(self, operation, expected_identity, expected_epoch):
        with self._lock:
            snapshot = self._snapshot_locked()
            if (
                not self._expected_matches(
                    snapshot,
                    expected_identity,
                    expected_epoch,
                )
                or not snapshot["playing"]
                or snapshot["paused"]
            ):
                return False
            self.pending_pause = {
                "operation": operation,
                "identity": expected_identity,
                "epoch": expected_epoch,
            }
            # The identity/epoch check and Kodi mutation are serialized under
            # the same re-entrant lock, closing the controller/adapter TOCTOU.
            self.pause()
            return True

    def request_resume(self, operation, expected_identity, expected_epoch):
        with self._lock:
            snapshot = self._snapshot_locked()
            if (
                not self._expected_matches(
                    snapshot,
                    expected_identity,
                    expected_epoch,
                )
                or not snapshot["playing"]
                or not snapshot["paused"]
            ):
                return False
            self.pending_resume = {
                "operation": operation,
                "identity": expected_identity,
                "epoch": expected_epoch,
            }
            self.pause()
            return True

    def request_seek(
        self,
        seconds,
        operation,
        expected_identity,
        expected_epoch,
    ):
        try:
            target = float(seconds)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(target):
            return False
        with self._lock:
            snapshot = self._snapshot_locked()
            if (
                not self._expected_matches(
                    snapshot,
                    expected_identity,
                    expected_epoch,
                )
                or not snapshot["seekable"]
            ):
                return False
            self.pending_seeks.append(
                {
                    "operation": operation,
                    "identity": expected_identity,
                    "epoch": expected_epoch,
                    "target": target,
                }
            )
            self.seekTime(target)
            return True

    def retire_operation(self, operation):
        """Forget an intent whose controller transaction no longer exists."""
        if operation is None:
            return
        with self._lock:
            if (
                self.pending_pause is not None
                and self.pending_pause["operation"] == operation
            ):
                self.pending_pause = None
            if (
                self.pending_resume is not None
                and self.pending_resume["operation"] == operation
            ):
                self.pending_resume = None
            self.pending_seeks = deque(
                intent
                for intent in self.pending_seeks
                if intent["operation"] != operation
            )

    def retire_operations(self, operations):
        for operation in tuple(operations or ()):
            self.retire_operation(operation)

    def _emit(self, kind, operation=None, extra=None, identity=None, epoch=None):
        if self.event_sink is None:
            return
        payload = {
            "operation": operation,
            "epoch": self.epoch if epoch is None else epoch,
            "identity": self._identity() if identity is None else identity,
        }
        if extra:
            payload.update(extra)
        self.event_sink(kind, payload)

    @staticmethod
    def _intent_matches(intent, identity, epoch):
        return (
            intent is not None
            and intent["identity"] == identity
            and intent["epoch"] == epoch
        )

    def onAVStarted(self):
        with self._lock:
            self.epoch += 1
            self.pending_pause = None
            self.pending_resume = None
            self.pending_seeks.clear()
            identity = self._identity()
            epoch = self.epoch
        self._emit("started", identity=identity, epoch=epoch)

    def onPlayBackPaused(self):
        with self._lock:
            identity = self._identity()
            epoch = self.epoch
            intent = self.pending_pause
            self.pending_pause = None
            operation = (
                intent["operation"]
                if self._intent_matches(intent, identity, epoch)
                else None
            )
        self._emit(
            "paused",
            operation,
            identity=identity,
            epoch=epoch,
        )

    def onPlayBackResumed(self):
        with self._lock:
            identity = self._identity()
            epoch = self.epoch
            intent = self.pending_resume
            self.pending_resume = None
            operation = (
                intent["operation"]
                if self._intent_matches(intent, identity, epoch)
                else None
            )
        self._emit(
            "resumed",
            operation,
            identity=identity,
            epoch=epoch,
        )

    def onPlayBackSeek(self, time_value, seek_offset):
        try:
            callback_target = float(time_value) / 1000.0
        except (TypeError, ValueError):
            callback_target = None
        with self._lock:
            identity = self._identity()
            epoch = self.epoch
            operation = None
            if callback_target is not None and math.isfinite(callback_target):
                intents = list(self.pending_seeks)
                candidates = [
                    (
                        abs(intent["target"] - callback_target),
                        index,
                        intent,
                    )
                    for index, intent in enumerate(intents)
                    if self._intent_matches(intent, identity, epoch)
                ]
                if candidates:
                    difference, index, intent = min(
                        candidates,
                        key=lambda candidate: (candidate[0], candidate[1]),
                    )
                    if difference <= SEEK_CALLBACK_TOLERANCE_SECONDS:
                        operation = intent["operation"]
                        del intents[index]
                        self.pending_seeks = deque(intents)
        self._emit(
            "seeked",
            operation,
            {
                "time": callback_target,
                "offset": seek_offset,
                "target": callback_target,
            },
            identity=identity,
            epoch=epoch,
        )

    def onPlayBackStopped(self):
        with self._lock:
            self.epoch += 1
            self.pending_pause = None
            self.pending_resume = None
            self.pending_seeks.clear()
            identity = self._identity()
            epoch = self.epoch
        self._emit("stopped", identity=identity, epoch=epoch)

    def onPlayBackEnded(self):
        with self._lock:
            self.epoch += 1
            self.pending_pause = None
            self.pending_resume = None
            self.pending_seeks.clear()
            identity = self._identity()
            epoch = self.epoch
        self._emit("ended", identity=identity, epoch=epoch)
