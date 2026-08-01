from __future__ import absolute_import, division, print_function

import math
import time

from seek_controller import (
    CANCEL_WAIT_PAUSE,
    COMMITTING,
    IDLE,
    PAUSE_PENDING,
    RESUME_PENDING,
    SCRUB_ACTIVE,
    SKIP_ACTIVE,
    SKIP_SETTLING,
    format_delta,
    format_time,
)


SETTLE_TOLERANCE_SECONDS = 1.5
SETTLE_STABLE_SAMPLES = 2
SETTLE_MAX_SECONDS = 4.0
PLAYING_SAMPLE_BACKWARD_TOLERANCE = 0.25
PLAYING_SAMPLE_MAX_ADVANCE = 5.0
PREVIEW_LOADING_DELAY_SECONDS = 0.180


def finite_number(value, default=None):
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(numeric):
        return default
    return numeric


def clamp(value, lower, upper):
    return max(lower, min(upper, value))


class PlaybackViewModel(object):
    """Pure semantic playback presentation state.

    Kodi's raw play position is an observation, not an authority while a seek
    is pending. This object latches the displayed actual position, target and
    operation watermarks so late/raw callbacks cannot make the UI regress.
    """

    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self.reset()

    def reset(self):
        self.identity = None
        self.epoch = None
        self.duration = 0.0
        self.actual = 0.0
        self.actual_revision = 0
        self.target = None
        self.target_revision = 0
        self.target_key = None
        self.controller_generation = None
        self.phase = "idle"
        self.active = False
        self.target_valid = False
        self.prompt = ""
        self.operations = {}
        self.settle_target = None
        self.settle_revision = None
        self.settle_started = None
        self.settle_samples = 0
        self.preview_status = "none"
        self.preview_path = ""
        self.preview_started_at = None
        self.hold_active = False
        self.hold_released = False
        self.last_update = None

    @staticmethod
    def _valid_player(snapshot):
        if not snapshot or not snapshot.get("identity"):
            return False
        if snapshot.get("epoch") is None:
            return False
        duration = finite_number(snapshot.get("duration"))
        return duration is not None and duration > 0.0

    def _same_media(self, snapshot):
        return (
            snapshot.get("identity") == self.identity
            and snapshot.get("epoch") == self.epoch
        )

    def _begin_media(self, snapshot):
        self.reset()
        self.identity = snapshot["identity"]
        self.epoch = snapshot["epoch"]
        self.duration = finite_number(snapshot.get("duration"), 0.0)
        self.actual = self._clamp_seconds(snapshot.get("current"), 0.0)

    def _clamp_seconds(self, value, default=None):
        numeric = finite_number(value, default)
        if numeric is None:
            return None
        upper = max(0.0, self.duration)
        return clamp(numeric, 0.0, upper)

    def _percent(self, seconds):
        if self.duration <= 0.0:
            return 0.0
        numeric = self._clamp_seconds(seconds, 0.0)
        return clamp((numeric * 100.0) / self.duration, 0.0, 100.0)

    def _clear_preview(self):
        self.preview_path = ""
        self.preview_status = "none"
        self.preview_started_at = None

    def _start_preview(self, now):
        self.preview_status = "loading" if self.preview_path else "none"
        self.preview_started_at = now

    def _advance_preview(self, now):
        if (
            not self.active
            or not self.target_valid
            or self.preview_status == "ready"
            or self.preview_started_at is None
        ):
            return
        elapsed = max(0.0, now - self.preview_started_at)
        if elapsed >= PREVIEW_LOADING_DELAY_SECONDS:
            if self.preview_status not in ("ready", "unavailable"):
                self.preview_status = "loading"
        elif self.preview_status not in ("loading", "unavailable"):
            self.preview_status = "none"

    def _clear_target(self):
        self.target = None
        self.target_valid = False
        self.target_key = None
        self.controller_generation = None
        self._clear_preview()

    def _set_target(self, controller_snapshot, now):
        target = self._clamp_seconds(controller_snapshot.get("target"))
        if target is None:
            target = self._clamp_seconds(
                controller_snapshot.get("target_seconds")
            )
        if target is None:
            self._clear_target()
            return

        generation = controller_snapshot.get("generation")
        key = (generation, round(target, 6))
        if key != self.target_key:
            self.target_revision += 1
            self.target_key = key
            self.target = target
            self._start_preview(now)
        else:
            self.target = target
        self.controller_generation = generation
        self.target_valid = True

    def _register_operations(self, controller_snapshot):
        skip_operation = controller_snapshot.get("skip_operation")
        skip_target = self._clamp_seconds(
            controller_snapshot.get("skip_requested_target")
        )
        if (
            skip_operation
            and skip_target is not None
            and skip_operation not in self.operations
        ):
            self.operations[skip_operation] = {
                "revision": self.target_revision,
                "target": skip_target,
            }

        if controller_snapshot.get("state") == COMMITTING:
            operation = controller_snapshot.get("pending_operation")
            if operation and operation not in self.operations:
                self.operations[operation] = {
                    "revision": self.target_revision,
                    "target": self.target,
                }
        else:
            operation = None

        live = set(
            item
            for item in (skip_operation, operation)
            if item is not None
        )
        self.operations = dict(
            (key, intent)
            for key, intent in self.operations.items()
            if key in live
        )

    def _observe_playing_actual(self, player_snapshot):
        if player_snapshot.get("paused"):
            return
        observed = self._clamp_seconds(player_snapshot.get("current"))
        if observed is None:
            return
        difference = observed - self.actual
        if (
            difference >= -PLAYING_SAMPLE_BACKWARD_TOLERANCE
            and difference <= PLAYING_SAMPLE_MAX_ADVANCE
        ):
            self.actual = max(self.actual, observed)

    def _settle(self, player_snapshot, now):
        observed = self._clamp_seconds(player_snapshot.get("current"))
        if (
            observed is not None
            and abs(observed - self.actual) <= SETTLE_TOLERANCE_SECONDS
        ):
            self.settle_samples += 1
        else:
            self.settle_samples = 0

        elapsed = (
            0.0
            if self.settle_started is None
            else max(0.0, now - self.settle_started)
        )
        if (
            self.settle_samples >= SETTLE_STABLE_SAMPLES
            or elapsed >= SETTLE_MAX_SECONDS
        ):
            if observed is not None:
                self.actual = observed
            self.active = False
            self._clear_target()
            self.phase = "idle"
            self.settle_target = None
            self.settle_revision = None
            self.settle_started = None
            self.settle_samples = 0
            self.operations = {}

    def _adopt_controller_handoff(self, controller_snapshot, now):
        """Latch a committed target even when Kodi omits the seek callback.

        The controller owns command attribution and exposes a short-lived,
        identity-bound handoff after an acknowledged or timed-out seek. The
        view model owns visual settlement. Keeping that boundary explicit
        prevents Kodi's old decoder position from becoming authoritative
        during the gap between command completion and clock convergence.
        """
        if not controller_snapshot.get("handoff_active"):
            return
        if (
            controller_snapshot.get("handoff_identity") != self.identity
            or controller_snapshot.get("handoff_epoch") != self.epoch
        ):
            return
        target = self._clamp_seconds(
            controller_snapshot.get("handoff_target")
        )
        if target is None:
            return

        generation = controller_snapshot.get("generation")
        rounded_target = round(target, 6)
        if (
            self.target_key is None
            or self.target_key[1] != rounded_target
        ):
            self.target_key = (generation, rounded_target)
            self.controller_generation = generation
            self._start_preview(now)

        same_settlement = (
            self.settle_target is not None
            and abs(self.settle_target - target)
            <= SETTLE_TOLERANCE_SECONDS
        )
        if not same_settlement:
            self.target_revision += 1
            self.settle_revision = self.target_revision
            self.settle_started = now
            self.settle_samples = 0
        elif self.settle_started is None:
            self.settle_started = now

        self.actual_revision = max(
            self.actual_revision,
            self.settle_revision or self.target_revision,
        )
        self.actual = target
        self.target = target
        self.target_valid = True
        self.settle_target = target
        self.phase = "settling"
        self.active = True

    def update(self, controller_snapshot, player_snapshot, now=None):
        """Consume immutable controller/player observations and return a view."""
        now = self.clock() if now is None else float(now)
        controller_snapshot = controller_snapshot or {}
        player_snapshot = player_snapshot or {}

        if not self._valid_player(player_snapshot):
            self.reset()
            return self.snapshot()
        if self.identity is None or not self._same_media(player_snapshot):
            self._begin_media(player_snapshot)

        duration = finite_number(player_snapshot.get("duration"))
        if duration is not None and duration > 0.0:
            self.duration = duration

        controller_active = bool(controller_snapshot.get("active"))
        controller_matches = (
            controller_snapshot.get("playback_epoch") == self.epoch
            and (
                not controller_snapshot.get("identity")
                or controller_snapshot.get("identity") == self.identity
            )
        )
        state = controller_snapshot.get("state", IDLE)
        self._adopt_controller_handoff(controller_snapshot, now)

        if controller_active and controller_matches:
            self._set_target(controller_snapshot, now)
            if not self.target_valid:
                self.active = False
                self.phase = "idle"
                self.prompt = ""
                self.operations = {}
                self.settle_target = None
                self.settle_revision = None
                self.settle_started = None
                self.settle_samples = 0
                self.last_update = now
                return self.snapshot()
            self._register_operations(controller_snapshot)
            self.hold_active = bool(controller_snapshot.get("hold"))
            self.hold_released = bool(
                controller_snapshot.get("hold_released")
            )
            self.prompt = ""

            if state == SKIP_ACTIVE:
                self.phase = "skip"
                self.active = True
                self._observe_playing_actual(player_snapshot)
            elif state == SKIP_SETTLING:
                self.phase = "applying"
                self.active = True
            elif state == PAUSE_PENDING:
                self.phase = "pausing"
                self.active = True
                self._observe_playing_actual(player_snapshot)
            elif state == SCRUB_ACTIVE:
                self.active = True
                if self.hold_active and not self.hold_released:
                    self.phase = "scrubbing"
                else:
                    self.phase = "ready"
                    self.prompt = "OK  Seek   \N{BULLET}   Back  Cancel"
            elif state == COMMITTING:
                self.phase = "applying"
                self.active = True
            elif state == CANCEL_WAIT_PAUSE:
                self.phase = "cancelling"
                self.active = False
                self._clear_target()
                self.prompt = ""
            elif state == RESUME_PENDING:
                reason = controller_snapshot.get("resume_reason")
                if reason in ("cancel", "pause-timeout"):
                    self.phase = "cancelling"
                    self.active = False
                    self._clear_target()
                else:
                    self.phase = "settling"
                    self.active = True
            self._advance_preview(now)
            self.last_update = now
            return self.snapshot()

        self.hold_active = False
        self.hold_released = False
        self.prompt = ""
        if self.settle_target is not None:
            self.phase = "settling"
            self.active = True
            self.target = self.settle_target
            self.target_valid = True
            self._settle(player_snapshot, now)
        else:
            self.actual = self._clamp_seconds(
                player_snapshot.get("current"),
                self.actual,
            )
            self.active = False
            self._clear_target()
            self.phase = "idle"
            self.operations = {}
        self._advance_preview(now)
        self.last_update = now
        return self.snapshot()

    def on_player_event(self, kind, payload=None, now=None):
        """Apply only attributed callbacks for this media and operation."""
        payload = payload or {}
        now = self.clock() if now is None else float(now)
        if kind in ("started", "stopped", "ended"):
            self.reset()
            return
        if self.identity is None:
            return
        if (
            payload.get("identity") != self.identity
            or payload.get("epoch") != self.epoch
        ):
            return
        if kind != "seeked":
            return

        operation = payload.get("operation")
        intent = self.operations.pop(operation, None)
        if intent is None:
            return
        revision = intent["revision"]
        if revision < self.actual_revision:
            return

        callback_target = self._clamp_seconds(payload.get("time"))
        requested_target = intent["target"]
        if (
            callback_target is None
            or abs(callback_target - requested_target)
            > SETTLE_TOLERANCE_SECONDS
        ):
            callback_target = requested_target
        self.actual_revision = revision
        self.actual = callback_target
        self.settle_target = callback_target
        self.settle_revision = revision
        self.settle_started = now
        self.settle_samples = 0

    def offer_preview(
        self,
        path,
        generation,
        target_seconds,
        producer_status="none",
    ):
        """Accept only an exact preview for the currently latched target."""
        if not self.active or not self.target_valid:
            return False
        if not path:
            if producer_status == "unavailable":
                self.preview_path = ""
                self.preview_status = "unavailable"
                return False
            if producer_status in (
                "initialising",
                "warming",
                "temporarily-failed",
            ):
                if self.preview_started_at is not None:
                    self._advance_preview(self.clock())
                return False
            if (
                self.phase == "settling"
                and self.preview_status == "ready"
                and self.preview_path
            ):
                # Controller state becomes idle as soon as the attributed seek
                # callback arrives, so the producer contract no longer
                # validates. Retain the last proven frame through the raw
                # decoder-position handoff instead of making it disappear.
                return True
            return False
        target = finite_number(target_seconds)
        if (
            generation != self.controller_generation
            or target is None
            or int(round(target)) != int(round(self.target))
        ):
            return False
        self.preview_path = str(path)
        self.preview_status = "ready"
        return True

    def snapshot(self):
        actual_percent = self._percent(self.actual)
        target_percent = (
            self._percent(self.target)
            if self.target_valid and self.target is not None
            else 0.0
        )
        target_time = (
            format_time(self.target)
            if self.target_valid and self.target is not None
            else ""
        )
        delta = (
            format_delta(self.target - self.actual)
            if self.target_valid and self.target is not None
            else ""
        )
        return {
            "active": self.active,
            "phase": self.phase,
            "identity": self.identity or "",
            "playback_epoch": self.epoch if self.epoch is not None else "",
            "controller_generation": self.controller_generation,
            "target_revision": self.target_revision,
            "actual_seconds": self.actual,
            "actual_percent": actual_percent,
            "target_valid": self.target_valid,
            "target_seconds": self.target if self.target_valid else None,
            "target_percent": target_percent,
            "time": target_time,
            "delta": delta,
            "prompt": self.prompt,
            "preview_status": self.preview_status,
            "preview_path": (
                self.preview_path
                if self.preview_status == "ready"
                else ""
            ),
            "hold_active": self.hold_active,
            "hold_released": self.hold_released,
        }
