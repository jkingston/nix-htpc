from __future__ import absolute_import, division, print_function

import math
import time


TAP_SECONDS = 10.0
AUTO_COMMIT_IDLE = 0.55
HOLD_ONSET_MIN = 0.28
HOLD_ONSET_MAX = 0.52
HOLD_REPEAT_MAX = 0.15
DENSE_GESTURE_GAP = 0.15
DENSE_GESTURE_EVENTS = 5
HOLD_PROBE_IDLE = 0.18
HOLD_RELEASE_IDLE = 0.23
SETTLE_MIN = 0.35
SETTLE_TIMEOUT = 4.0
SETTLE_TOLERANCE = 2.0
MAX_INTEGRATION_STEP = 0.20


def clamp(value, lower, upper):
    return max(lower, min(upper, value))


def format_time(seconds):
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, seconds)
    return "%d:%02d" % (minutes, seconds)


def format_delta(seconds):
    sign = "+" if seconds >= 0 else "\N{MINUS SIGN}"
    return sign + format_time(abs(seconds))


def hold_velocity(elapsed, duration):
    """Return seconds of media traversed per real second of a held button."""
    cap = min(600.0, max(60.0, float(duration) * 0.10))
    return min(cap, 10.0 * math.pow(2.0, max(0.0, elapsed) / 1.25))


class RepeatGuard(object):
    """Accept one semantic action after a complete quiet period."""

    def __init__(self, quiet_period=0.50):
        self.quiet_period = float(quiet_period)
        self.deadlines = {}

    def accept(self, action, now):
        now = float(now)
        deadline = self.deadlines.get(action)
        self.deadlines[action] = now + self.quiet_period
        return deadline is None or now >= deadline

    def reset(self):
        self.deadlines = {}


class SeekController(object):
    """Pure seek transaction state machine.

    The player object supplies is_seekable(), get_time(), get_duration(),
    is_paused(), seek(seconds), and ensure_playing(). The publisher supplies
    publish(snapshot) and clear().
    """

    def __init__(self, player, publisher, clock=None):
        self.player = player
        self.publisher = publisher
        self.clock = clock or time.monotonic
        self.generation = 0
        self.reset()

    @property
    def active(self):
        return self.state != "idle"

    @property
    def manual(self):
        return self.state in ("hold", "hold-pending", "timeline")

    def reset(self):
        self.state = "idle"
        self.source = None
        self.origin = 0.0
        self.target = 0.0
        self.duration = 0.0
        self.was_paused = False
        self.item_identity = None
        self.last_input = None
        self.last_direction = 0
        self.last_tap_time = None
        self.probe_times = []
        self.probe_direction = 0
        self.probe_started = None
        self.hold_candidate_until = None
        self.hold_started = None
        self.last_integrated = None
        self.dense_repeats = 0
        self.dense_sequence_count = 0
        self.dense_sequence_anchor = 0.0
        self.settle_not_before = None
        self.settle_deadline = None
        self.publisher.clear()

    def _start(self, direction, now, source, base=None):
        if not self.player.is_seekable():
            return False

        try:
            duration = float(self.player.get_duration())
            current = float(self.player.get_time()) if base is None else float(base)
            was_paused = bool(self.player.is_paused())
            item_identity = self.player.get_identity()
        except Exception:
            return False

        if duration <= 0:
            return False

        self.generation += 1
        self.state = "timeline" if source == "timeline" else "tap"
        self.source = source
        self.origin = clamp(current, 0.0, duration)
        self.duration = duration
        self.target = self._clamp_target(
            self.origin + (direction * TAP_SECONDS)
        )
        self.was_paused = was_paused
        self.item_identity = item_identity
        self.last_input = now
        self.last_direction = direction
        self.last_tap_time = now
        self.probe_times = []
        self.probe_direction = 0
        self.probe_started = None
        self.hold_candidate_until = None
        self.hold_started = None
        self.last_integrated = None
        self.dense_repeats = 0
        self.dense_sequence_count = 1
        self.dense_sequence_anchor = self.target
        self.settle_not_before = None
        self.settle_deadline = None
        self._publish()
        return True

    def arrow(self, direction, source="fullscreen", now=None):
        now = self.clock() if now is None else float(now)
        direction = -1 if direction < 0 else 1

        if self.state == "settling":
            return self._start(direction, now, source, base=self.target)
        if self.state == "idle":
            return self._start(direction, now, source)
        if self.state == "tap":
            self._tap_arrow(direction, now)
        elif self.state == "timeline":
            self._timeline_arrow(direction, now)
        elif self.state == "hold":
            self._hold_arrow(direction, now)
        elif self.state == "hold-pending":
            if direction != self.last_direction:
                self.target = self._clamp_target(
                    self.target + (direction * TAP_SECONDS)
                )
            else:
                self.target = self._clamp_target(
                    self.target + (direction * TAP_SECONDS)
                )
            self.state = "timeline"
            self.source = "timeline"
            self.last_input = now
            self.last_direction = direction
            self.last_tap_time = now
            self.dense_repeats = 0
            self._publish()
        return True

    def _tap_arrow(self, direction, now):
        gap = None if self.last_input is None else now - self.last_input
        same_direction = direction == self.last_direction
        if (
            same_direction
            and gap is not None
            and gap <= DENSE_GESTURE_GAP
        ):
            self.dense_sequence_count += 1
        else:
            self.dense_sequence_count = 1
            self.dense_sequence_anchor = self.target
        self.last_input = now

        # CEC does not expose a trustworthy long-press modifier. A bounded
        # dense train is therefore the fail-safe hold signature even when a
        # dropped repeat makes the initial cadence miss the primary probe.
        if self.dense_sequence_count >= DENSE_GESTURE_EVENTS:
            self.target = self.dense_sequence_anchor
            self._begin_hold(direction, now, now)
            return

        if self.probe_times:
            probe_gap = now - self.probe_times[-1]
            if direction == self.probe_direction and probe_gap <= HOLD_REPEAT_MAX:
                self.probe_times.append(now)
                if len(self.probe_times) >= 3:
                    self._begin_hold(direction, now, self.probe_started)
                return

            self._materialize_probe()
            gap = now - self.last_tap_time

        if (
            same_direction
            and gap is not None
            and gap <= HOLD_REPEAT_MAX
            and (
                self.dense_sequence_count >= 2
                or (
                    self.hold_candidate_until is not None
                    and now <= self.hold_candidate_until
                )
            )
        ):
            self.probe_times = [now]
            self.probe_direction = direction
            self.probe_started = now
            self._publish()
            return

        if (
            same_direction
            and gap is not None
            and HOLD_ONSET_MIN <= gap <= HOLD_ONSET_MAX
        ):
            self.probe_times = [now]
            self.probe_direction = direction
            self.probe_started = now
            self.hold_candidate_until = now + 1.20
            self._publish()
            return

        self.target = self._clamp_target(
            self.target + (direction * TAP_SECONDS)
        )
        self.last_direction = direction
        self.last_tap_time = now
        if not same_direction:
            self.hold_candidate_until = None
        self._publish()

    def _timeline_arrow(self, direction, now):
        # Explicit timeline mode changes commit semantics, not gesture
        # recognition. Reuse the same buffered tap/hold classifier.
        self._tap_arrow(direction, now)

    def _begin_hold(self, direction, now, started):
        self.probe_times = []
        self.probe_direction = 0
        self.probe_started = None
        self.hold_candidate_until = None
        self.state = "hold"
        self.source = "hold"
        self.last_direction = direction
        self.last_input = now
        self.hold_started = started if started is not None else now
        self.last_integrated = now
        self.dense_repeats = 0
        self._publish()

    def _hold_arrow(self, direction, now):
        self._integrate_hold(now)
        if direction != self.last_direction:
            self.target = self._clamp_target(
                self.target + (direction * TAP_SECONDS)
            )
            self.last_direction = direction
            self.hold_started = now
            self.last_integrated = now
        self.last_input = now
        self._publish()

    def _integrate_hold(self, now):
        if self.state != "hold" or self.last_integrated is None:
            return

        # Input repeats end at release. Integrate only a short grace period
        # beyond the most recent physical event while waiting to infer key-up.
        endpoint = min(now, self.last_input + 0.12)
        cursor = self.last_integrated
        while cursor < endpoint:
            next_cursor = min(endpoint, cursor + MAX_INTEGRATION_STEP)
            elapsed = cursor - self.hold_started
            velocity = hold_velocity(elapsed, self.duration)
            self.target = self._clamp_target(
                self.target
                + (self.last_direction * velocity * (next_cursor - cursor))
            )
            cursor = next_cursor
        self.last_integrated = max(self.last_integrated, endpoint)

    def tick(self, now=None):
        now = self.clock() if now is None else float(now)

        if self.state == "idle":
            return
        try:
            seekable = self.player.is_seekable()
            identity = self.player.get_identity()
        except Exception:
            self.reset()
            return
        if not seekable or identity != self.item_identity:
            self.reset()
            return

        if self.state in ("tap", "timeline"):
            if self.probe_times and now - self.probe_times[-1] >= HOLD_PROBE_IDLE:
                self._materialize_probe()
            if self.state == "tap" and (
                not self.probe_times
                and self.last_input is not None
                and now - self.last_input >= AUTO_COMMIT_IDLE
            ):
                self.commit(play_after=False, now=now)
        elif self.state == "hold":
            self._integrate_hold(now)
            if now - self.last_input >= HOLD_RELEASE_IDLE:
                self.state = "hold-pending"
                self._publish()
            else:
                self._publish()
        elif self.state == "settling":
            if now < self.settle_not_before:
                return
            try:
                current = float(self.player.get_time())
            except Exception:
                current = None
            if (
                current is not None
                and abs(current - self.target) <= SETTLE_TOLERANCE
            ) or now >= self.settle_deadline:
                self.reset()

    def _materialize_probe(self):
        for _unused in self.probe_times:
            self.target = self._clamp_target(
                self.target + (self.probe_direction * TAP_SECONDS)
            )
        if self.probe_times:
            self.last_tap_time = self.probe_times[-1]
        self.probe_times = []
        self.probe_direction = 0
        self.probe_started = None
        self._publish()

    def confirm(self, now=None):
        now = self.clock() if now is None else float(now)
        if self.state in ("hold", "hold-pending", "timeline"):
            if self.state == "hold":
                self._integrate_hold(now)
            self.commit(play_after=True, now=now)
            return True
        return False

    def commit(self, play_after, now=None):
        now = self.clock() if now is None else float(now)
        if self.state == "idle":
            return False

        try:
            valid = (
                self.player.is_seekable()
                and self.player.get_identity() == self.item_identity
            )
        except Exception:
            valid = False
        if not valid:
            self.reset()
            return False

        if self.state == "tap" and self.probe_times:
            self._materialize_probe()

        self.player.seek(self.target)
        if play_after:
            self.player.ensure_playing()

        self.generation += 1
        self.state = "settling"
        self.source = "settling"
        self.last_input = now
        self.probe_times = []
        self.settle_not_before = now + SETTLE_MIN
        self.settle_deadline = now + SETTLE_TIMEOUT
        self._publish()
        return True

    def cancel(self):
        if not self.active:
            return False
        self.generation += 1
        self.reset()
        return True

    def _clamp_target(self, value):
        # Several Kodi player cores reject an exact-duration seek. If playback
        # is already inside the final second, Right remains a no-op rather than
        # moving the target backwards.
        upper = max(0.0, self.duration - 1.0)
        if self.origin > upper and value >= self.origin:
            upper = self.origin
        return clamp(value, 0.0, upper)

    def snapshot(self):
        percent = 0.0
        if self.duration > 0:
            percent = (self.target * 100.0) / self.duration
        return {
            "active": self.active,
            "generation": self.generation,
            "state": self.state,
            "source": self.source or "",
            "target": self.target,
            "target_seconds": int(round(self.target)),
            "percent": clamp(percent, 0.0, 100.0),
            "time": format_time(self.target),
            "delta": format_delta(self.target - self.origin),
            "confirm": self.manual,
        }

    def _publish(self):
        self.publisher.publish(self.snapshot())
