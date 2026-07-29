from __future__ import absolute_import, division, print_function

import math
import time


STEP_SECONDS = 10.0
SKIP_IDLE = 0.55
SKIP_SETTLE_TIMEOUT = 4.0
HOLD_ONSET_MIN = 0.28
HOLD_ONSET_MAX = 0.52
HOLD_REPEAT_MAX = 0.15
HOLD_PROBE_IDLE = 0.18
HOLD_CONFIRM_EVENTS = 3
HOLD_RELEASE_IDLE = 0.23
MAX_INTEGRATION_STEP = 0.20
PAUSE_TIMEOUT = 0.75
LATE_PAUSE_TIMEOUT = 2.0
SEEK_TIMEOUT = 4.0
RESUME_TIMEOUT = 1.0

IDLE = "idle"
SKIP_ACTIVE = "skip-active"
SKIP_SETTLING = "skip-settling"
PAUSE_PENDING = "pause-pending"
SCRUB_ACTIVE = "scrub-active"
CANCEL_WAIT_PAUSE = "cancel-wait-pause"
COMMITTING = "committing"
RESUME_PENDING = "resume-pending"


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
    """Gradual scrub speed, independent of CEC repeat frequency."""
    cap = min(600.0, max(60.0, float(duration) * 0.10))
    return min(cap, 10.0 * math.pow(2.0, max(0.0, elapsed) / 1.75))


class RepeatGuard(object):
    """Collapse a repeat train into one semantic Select or Back action."""

    def __init__(self, quiet_period=0.50):
        self.quiet_period = float(quiet_period)
        self.deadlines = {}

    def accept(self, action, now):
        now = float(now)
        deadline = self.deadlines.get(action)
        self.deadlines[action] = now + self.quiet_period
        return deadline is None or now >= deadline

    def arm(self, action, now):
        """Suppress a train already consumed by another modal input layer."""
        self.deadlines[action] = float(now) + self.quiet_period

    def reset(self):
        self.deadlines = {}


class SeekController(object):
    """Coalesced fixed skips plus a pause-owned modal scrub.

    The first fullscreen event moves the optimistic target by ten seconds. A
    quiet gesture commits exactly one absolute player seek. A measured CEC
    onset/repeat signature promotes to scrub before committing; ambiguous probe
    events stay buffered so promotion never rolls the visible target backwards.
    If the probe fails, every buffered event materializes as an exact ten-second
    tap.

    The player supplies identity-bound request_pause/request_seek/
    request_resume commands plus retire_operation(). Kodi pause is a toggle,
    so automatic resume requires a matching pause callback.
    """

    def __init__(self, player, publisher, clock=None):
        self.player = player
        self.publisher = publisher
        self.clock = clock or time.monotonic
        self.generation = 0
        self.operation_sequence = 0
        self.issued_operations = set()
        self._clear_fields()
        self.publisher.clear()

    @property
    def active(self):
        return self.state != IDLE

    @property
    def manual(self):
        return self.state in (
            PAUSE_PENDING,
            SCRUB_ACTIVE,
            CANCEL_WAIT_PAUSE,
            COMMITTING,
            RESUME_PENDING,
        )

    @property
    def confirmable(self):
        return self.state == SCRUB_ACTIVE

    def _clear_fields(self):
        self.state = IDLE
        self.source = ""
        self.origin = 0.0
        self.target = 0.0
        self.duration = 0.0
        self.identity = None
        self.epoch = None
        self.was_playing = False
        self.controller_paused = False
        self.pending_operation = None
        self.pending_deadline = None
        self.resume_reason = None
        self.confirm_when_paused = False
        self.confirm_after_skip = False
        self.last_input = None
        self.gesture_direction = 0
        self.probe_count = 0
        self.hold_active = False
        self.hold_released = False
        self.hold_started = None
        self.last_integrated = None
        self.skip_inflight = None
        self.skip_deadline = None
        self.skip_flush_requested = False

    def _next_operation(self, kind):
        self.operation_sequence += 1
        return "%s:%d:%d" % (kind, self.generation, self.operation_sequence)

    def _track_operation(self, operation):
        self.issued_operations.add(operation)

    def _retire_operation(self, operation):
        if operation is None:
            return
        self.issued_operations.discard(operation)
        try:
            self.player.retire_operation(operation)
        except Exception:
            # AttributeError keeps the controller usable with minimal test or
            # third-party adapters; a retirement failure must never mutate a
            # different playback item.
            pass

    def _complete_operation(self, operation):
        # The adapter normally consumes a matching callback intent itself.
        # Explicit retirement is idempotent and also covers alternate adapters.
        self._retire_operation(operation)

    def _retire_all_operations(self):
        operations = tuple(self.issued_operations)
        self.issued_operations.clear()
        if not operations:
            return
        try:
            self.player.retire_operations(operations)
            return
        except Exception:
            pass
        for operation in operations:
            try:
                self.player.retire_operation(operation)
            except Exception:
                pass

    @staticmethod
    def _valid_snapshot(snapshot):
        return (
            bool(snapshot)
            and bool(snapshot.get("seekable"))
            and float(snapshot.get("duration", 0.0)) > 0.0
            and bool(snapshot.get("identity"))
            and snapshot.get("epoch") is not None
        )

    def _snapshot_matches(self, snapshot):
        return (
            self._valid_snapshot(snapshot)
            and snapshot.get("identity") == self.identity
            and snapshot.get("epoch") == self.epoch
        )

    def _read_snapshot(self):
        try:
            return self.player.snapshot()
        except Exception:
            return None

    def _capture(self, snapshot, source, base=None):
        self.generation += 1
        self.source = source
        current = (
            float(snapshot.get("current", 0.0))
            if base is None
            else float(base)
        )
        self.origin = clamp(current, 0.0, float(snapshot["duration"]))
        self.target = self.origin
        self.duration = float(snapshot["duration"])
        self.identity = snapshot["identity"]
        self.epoch = snapshot["epoch"]
        self.was_playing = not bool(snapshot.get("paused"))
        self.controller_paused = False

    def hidden_step(self, direction, now=None):
        """Route a fullscreen arrow through the shared tap/hold classifier."""
        return self._optimistic_step(direction, "fullscreen", now)

    def timeline_step(self, direction, now=None):
        """Route a focused-timeline arrow through the same classifier.

        Timeline focus changes presentation, not seek semantics: isolated
        arrows remain automatic fixed skips and only a proven repeat cadence
        promotes to a pause-owned scrub.
        """
        return self._optimistic_step(direction, "timeline", now)

    def _optimistic_step(self, direction, source, now=None):
        """Update an optimistic exact-tap target; do not seek immediately."""
        now = self.clock() if now is None else float(now)
        direction = -1 if direction < 0 else 1

        if self.state == IDLE:
            snapshot = self._read_snapshot()
            if not self._valid_snapshot(snapshot):
                return False
            self._capture(snapshot, source)
            self.state = SKIP_ACTIVE
            self.gesture_direction = direction
            self.last_input = now
            self._apply_steps(direction, 1)
            self._publish()
            return True

        if self.state == SKIP_SETTLING:
            if not self._snapshot_matches(self._read_snapshot()):
                self.reset()
                return False
            # A new gesture can accumulate from the last logical target while
            # the decoder acknowledges the previous coalesced seek.
            self.state = SKIP_ACTIVE
            self.source = source
            self.probe_count = 0
            self.last_input = now
            self.gesture_direction = direction
            self._apply_steps(direction, 1)
            self._publish()
            return True

        if self.state == SKIP_ACTIVE:
            if not self._snapshot_matches(self._read_snapshot()):
                self.reset()
                return False
            if self.skip_flush_requested:
                # The queued flush belongs to the quiet/explicit boundary
                # observed before this arrow. This input advances the logical
                # target and starts/continues a newer gesture, so the old
                # acknowledgement must only retire its in-flight operation;
                # it must not flush or reset the newer classifier.
                self.skip_flush_requested = False
            if self.last_input is not None and now - self.last_input > SKIP_IDLE:
                self.flush_skip(now)
                if self.state == SKIP_ACTIVE:
                    # A prior absolute seek is still awaiting its callback.
                    # Start the separated gesture at the logical target and
                    # serialize its eventual commit behind that operation.
                    # The just-requested flush covers the gesture that ended;
                    # this arrow is already the next watermark.
                    self.skip_flush_requested = False
                    self.source = source
                    self.probe_count = 0
                    self.last_input = now
                    self.gesture_direction = direction
                    self._apply_steps(direction, 1)
                    self._publish()
                    return True
                return self._optimistic_step(direction, source, now)
            if self._record_discrete_or_probe(direction, now):
                self._promote_optimistic_hold(now)
            self.source = "hold" if self.hold_active else source
            self._publish()
            return True

        if self.state in (PAUSE_PENDING, SCRUB_ACTIVE) and self.source == "hold":
            return self._scrub_event(direction, now)
        return False

    def arrow(self, direction, source="timeline", now=None):
        if source == "timeline":
            return self.timeline_step(direction, now)
        if source in ("fullscreen", "hidden"):
            return self.hidden_step(direction, now)
        return False

    def begin_chapter_browse(self, now=None):
        """Chapter rail is a zero-delta pause-owned seek transaction."""
        now = self.clock() if now is None else float(now)
        if self.state != IDLE:
            return False
        snapshot = self._read_snapshot()
        if not self._valid_snapshot(snapshot):
            return False
        self._capture(snapshot, "chapter")
        self.last_input = now
        if snapshot.get("paused"):
            self.state = SCRUB_ACTIVE
            self._publish()
        else:
            self._request_pause(now)
        return True

    def set_target(self, seconds):
        if self.state not in (PAUSE_PENDING, SCRUB_ACTIVE):
            return False
        if not self._snapshot_matches(self._read_snapshot()):
            self.reset()
            return False
        self.target = self._clamp_target(float(seconds))
        self._publish()
        return True

    def _record_discrete_or_probe(self, direction, now):
        """Return True only when the buffered CEC signature proves a hold."""
        if self.last_input is None:
            self.gesture_direction = direction
            self.last_input = now
            self._apply_steps(direction, 1)
            return False

        gap = now - self.last_input
        same_direction = direction == self.gesture_direction

        if self.probe_count:
            if same_direction and gap <= HOLD_REPEAT_MAX:
                self.probe_count += 1
                self.last_input = now
                if self.probe_count >= HOLD_CONFIRM_EVENTS:
                    self.probe_count = 0
                    return True
                return False
            self._materialize_probe()

        if not same_direction:
            self.gesture_direction = direction
            self._apply_steps(direction, 1)
        elif HOLD_ONSET_MIN <= gap <= HOLD_ONSET_MAX:
            # Buffer the onset event and following repeat probes. Their target
            # is materialized only if the signature fails.
            self.probe_count = 1
        else:
            self._apply_steps(direction, 1)
        self.last_input = now
        return False

    def _materialize_probe(self):
        if self.probe_count:
            self._apply_steps(self.gesture_direction, self.probe_count)
            self.probe_count = 0

    def _apply_steps(self, direction, count):
        self.target = self._clamp_target(
            self.target + (direction * STEP_SECONDS * int(count))
        )

    def _request_pause(self, now):
        operation = self._next_operation("pause")
        self._track_operation(operation)
        self.state = PAUSE_PENDING
        self.pending_operation = operation
        self.pending_deadline = now + PAUSE_TIMEOUT
        self._publish()
        try:
            issued = bool(
                self.player.request_pause(
                    operation,
                    self.identity,
                    self.epoch,
                )
            )
        except Exception:
            issued = False
        if issued:
            return
        self._retire_operation(operation)
        latest = self._read_snapshot()
        if self._snapshot_matches(latest) and latest.get("paused"):
            self.state = SCRUB_ACTIVE
            self.pending_operation = None
            self.pending_deadline = None
            self.controller_paused = False
            if self.hold_active:
                self._start_hold_clock(now)
            self._publish()
        else:
            self.reset()

    def _promote_optimistic_hold(self, now):
        self.source = "hold"
        self.hold_active = True
        self.hold_released = False
        snapshot = self._read_snapshot()
        if not self._snapshot_matches(snapshot):
            self.reset()
            return
        if snapshot.get("paused"):
            self.state = SCRUB_ACTIVE
            self.controller_paused = False
            self._start_hold_clock(now)
        else:
            self._request_pause(now)

    def _scrub_event(self, direction, now):
        if not self._snapshot_matches(self._read_snapshot()):
            self.reset()
            return False
        if self.hold_active and self.hold_released:
            # A quiet period ended the previous hold. The next press is a new
            # fine-grained gesture even in the same direction; never integrate
            # across the gap or inherit the old acceleration ramp.
            self.hold_active = False
            self.hold_released = False
            self.hold_started = None
            self.last_integrated = None
            self.probe_count = 0
            self.gesture_direction = direction
            self.last_input = now
            self._apply_steps(direction, 1)
            self._publish()
            return True
        if self.hold_active:
            if self.state == SCRUB_ACTIVE:
                self._integrate_hold(now)
            if direction != self.gesture_direction:
                self._apply_steps(direction, 1)
                self.gesture_direction = direction
                self._start_hold_clock(now)
            self.last_input = now
            self.hold_released = False
            self._publish()
            return True

        if self._record_discrete_or_probe(direction, now):
            self.hold_active = True
            self.hold_released = False
            if self.state == SCRUB_ACTIVE:
                self._start_hold_clock(now)
        self._publish()
        return True

    def _start_hold_clock(self, now):
        self.hold_started = now
        self.last_integrated = now

    def _integrate_hold(self, now):
        if (
            not self.hold_active
            or self.hold_released
            or self.last_integrated is None
            or self.last_input is None
        ):
            return
        endpoint = min(now, self.last_input + 0.12)
        cursor = self.last_integrated
        while cursor < endpoint:
            next_cursor = min(endpoint, cursor + MAX_INTEGRATION_STEP)
            elapsed = cursor - self.hold_started
            velocity = hold_velocity(elapsed, self.duration)
            self.target = self._clamp_target(
                self.target
                + (
                    self.gesture_direction
                    * velocity
                    * (next_cursor - cursor)
                )
            )
            cursor = next_cursor
        self.last_integrated = max(self.last_integrated, endpoint)

    def flush_skip(self, now=None):
        """Commit the current optimistic target once, preserving play state."""
        now = self.clock() if now is None else float(now)
        if self.state not in (SKIP_ACTIVE, SKIP_SETTLING):
            return False
        self._materialize_probe()
        if self.skip_inflight is not None:
            self.skip_flush_requested = True
            self._publish()
            return True

        operation = self._next_operation("skip")
        self._track_operation(operation)
        self.skip_inflight = operation
        self.skip_deadline = now + SKIP_SETTLE_TIMEOUT
        self.skip_flush_requested = False
        self.state = SKIP_SETTLING
        self._publish()
        try:
            issued = bool(
                self.player.request_seek(
                    self.target,
                    operation,
                    self.identity,
                    self.epoch,
                )
            )
        except Exception:
            issued = False
        if not issued:
            self._retire_operation(operation)
            self.reset()
            return False
        return True

    def end_optimistic_skip(self, now=None):
        return self.flush_skip(now)

    def confirm(self, now=None):
        now = self.clock() if now is None else float(now)
        if self.state == PAUSE_PENDING:
            self.confirm_when_paused = True
            self._publish()
            return True
        if self.state != SCRUB_ACTIVE:
            return False
        if self.skip_inflight is not None:
            self.confirm_after_skip = True
            self._publish()
            return True
        self._materialize_probe()
        if self.hold_active:
            self._integrate_hold(now)
        if not self._snapshot_matches(self._read_snapshot()):
            self.reset()
            return False

        operation = self._next_operation("seek")
        self._track_operation(operation)
        self.state = COMMITTING
        self.pending_operation = operation
        self.pending_deadline = now + SEEK_TIMEOUT
        self._publish()
        try:
            issued = bool(
                self.player.request_seek(
                    self.target,
                    operation,
                    self.identity,
                    self.epoch,
                )
            )
        except Exception:
            issued = False
        if not issued:
            self._retire_operation(operation)
            self._finish_commit(now)
        return True

    def cancel(self, now=None):
        now = self.clock() if now is None else float(now)
        if self.state == IDLE:
            return False
        if self.state == SKIP_ACTIVE:
            # Back means abandon the still-optimistic target; it must never
            # turn navigation into an unexpected seek.
            self.reset()
            return True
        if self.state == SKIP_SETTLING:
            # Kodi is already processing the sole absolute seek. Clear the UI
            # and retire its tag without queuing a follow-up seek.
            self.reset()
            return True
        if self.state == PAUSE_PENDING:
            self.confirm_when_paused = False
            self.state = CANCEL_WAIT_PAUSE
            self.pending_deadline = now + LATE_PAUSE_TIMEOUT
            self._publish()
            return True
        if self.state == CANCEL_WAIT_PAUSE:
            return True
        if self.state == SCRUB_ACTIVE:
            if self.controller_paused and self.was_playing:
                self._request_resume("cancel", now)
            else:
                self.reset()
            return True
        if self.state in (COMMITTING, RESUME_PENDING):
            return True
        return False

    def on_player_event(self, kind, payload=None, now=None):
        payload = payload or {}
        now = self.clock() if now is None else float(now)

        if kind in ("started", "stopped", "ended"):
            self.reset()
            return
        if not self.active:
            return
        if payload.get("epoch") is not None and payload.get("epoch") != self.epoch:
            self.reset()
            return
        if (
            payload.get("identity") is not None
            and payload.get("identity") != self.identity
        ):
            self.reset()
            return

        operation = payload.get("operation")
        if (
            kind == "seeked"
            and operation is not None
            and operation == self.skip_inflight
        ):
            self._complete_operation(operation)
            self.skip_inflight = None
            self.skip_deadline = None
            if self.state == SKIP_SETTLING:
                self.reset()
            elif self.state == SKIP_ACTIVE and self.skip_flush_requested:
                self.skip_flush_requested = False
                self.flush_skip(now)
            elif self.state == SCRUB_ACTIVE and self.confirm_after_skip:
                self.confirm_after_skip = False
                self.confirm(now)
            return

        if kind == "paused":
            if self.state == PAUSE_PENDING:
                expected = self.pending_operation
                if operation is None or operation != expected:
                    return
                self.controller_paused = operation == expected
                self._retire_operation(expected)
                self.state = SCRUB_ACTIVE
                self.pending_operation = None
                self.pending_deadline = None
                if self.hold_active:
                    self._start_hold_clock(now)
                confirm = self.confirm_when_paused
                self.confirm_when_paused = False
                self._publish()
                if confirm:
                    self.confirm(now)
                return
            if self.state == CANCEL_WAIT_PAUSE:
                expected = self.pending_operation
                if operation is None or operation != expected:
                    return
                self._retire_operation(expected)
                if self.was_playing:
                    self.controller_paused = True
                    self._request_resume("cancel", now)
                else:
                    self.reset()
                return
            if operation != self.pending_operation:
                self.controller_paused = False
            return

        if kind == "resumed":
            if self.state == RESUME_PENDING:
                if operation is None or operation != self.pending_operation:
                    return
                self._complete_operation(operation)
                self.reset()
            elif self.state in (PAUSE_PENDING, SCRUB_ACTIVE):
                self.reset()
            return

        if kind == "seeked" and self.state == COMMITTING:
            # An external or late untagged seek must not commit this target.
            if operation is None or operation != self.pending_operation:
                return
            self._complete_operation(operation)
            self._finish_commit(now)

    def tick(self, now=None):
        now = self.clock() if now is None else float(now)
        if self.state == IDLE:
            return
        snapshot = self._read_snapshot()
        if not self._snapshot_matches(snapshot):
            self.reset()
            return

        # A second fixed-skip gesture may begin before Kodi acknowledges the
        # previous absolute seek. Expire that independent operation before any
        # state-specific early return, otherwise SKIP_ACTIVE can strand it and
        # prevent the new target from ever being committed.
        if (
            self.skip_inflight is not None
            and self.skip_deadline is not None
            and now >= self.skip_deadline
        ):
            self._retire_operation(self.skip_inflight)
            self.skip_inflight = None
            self.skip_deadline = None
            if self.state == SKIP_SETTLING:
                self.reset()
                return
            if self.state == SCRUB_ACTIVE and self.confirm_after_skip:
                self.confirm_after_skip = False
                self.confirm(now)

        if (
            self.probe_count
            and self.last_input is not None
            and now - self.last_input >= HOLD_PROBE_IDLE
        ):
            self._materialize_probe()
            self._publish()

        if self.state == SKIP_ACTIVE:
            if self.last_input is not None and now - self.last_input >= SKIP_IDLE:
                self.flush_skip(now)
            return

        if self.state == SCRUB_ACTIVE and self.hold_active:
            self._integrate_hold(now)
            if (
                not self.hold_released
                and self.last_input is not None
                and now - self.last_input >= HOLD_RELEASE_IDLE
            ):
                self._integrate_hold(self.last_input + 0.12)
                self.hold_released = True
            self._publish()

        if self.state == PAUSE_PENDING and now >= self.pending_deadline:
            self._finish_missing_pause(now)
        elif self.state == CANCEL_WAIT_PAUSE and now >= self.pending_deadline:
            self._finish_missing_pause(now)
        elif self.state == COMMITTING and now >= self.pending_deadline:
            self._retire_operation(self.pending_operation)
            self.pending_operation = None
            self._finish_commit(now)
        elif self.state == RESUME_PENDING and now >= self.pending_deadline:
            self.reset()

    def _finish_commit(self, now):
        if self.controller_paused and self.was_playing:
            self._request_resume("commit", now)
        else:
            self.reset()

    def _finish_missing_pause(self, now):
        """Retire a missing callback without leaving our toggle paused.

        Kodi's pause mutation is synchronous while its Python callback can be
        delayed or omitted. If the same identity/epoch is now paused, the
        validated toggle issued by this controller owns the transition and is
        safely unwound. If it is still playing, the intent is simply retired.
        """
        expected = self.pending_operation
        self._retire_operation(expected)
        self.pending_operation = None
        snapshot = self._read_snapshot()
        if (
            self.was_playing
            and self._snapshot_matches(snapshot)
            and snapshot.get("paused")
        ):
            self.controller_paused = True
            self._request_resume("pause-timeout", now)
        else:
            self.reset()

    def _request_resume(self, reason, now):
        snapshot = self._read_snapshot()
        if not self._snapshot_matches(snapshot) or not snapshot.get("paused"):
            self.reset()
            return
        operation = self._next_operation("resume")
        self._track_operation(operation)
        self.state = RESUME_PENDING
        self.pending_operation = operation
        self.pending_deadline = now + RESUME_TIMEOUT
        self.resume_reason = reason
        self._publish()
        try:
            issued = bool(
                self.player.request_resume(
                    operation,
                    self.identity,
                    self.epoch,
                )
            )
        except Exception:
            issued = False
        if not issued:
            self._retire_operation(operation)
            self.reset()

    def shutdown(self, now=None):
        now = self.clock() if now is None else float(now)
        if self.controller_paused and self.was_playing:
            self._request_resume("shutdown", now)
        else:
            self.reset()

    def reset(self):
        self._retire_all_operations()
        if self.state != IDLE:
            self.generation += 1
        self._clear_fields()
        self.publisher.clear()

    def _clamp_target(self, value):
        upper = max(0.0, self.duration - 1.0)
        if self.origin > upper and value >= self.origin:
            upper = self.origin
        return clamp(value, 0.0, upper)

    def snapshot(self):
        percent = 0.0
        if self.duration > 0:
            percent = (self.target * 100.0) / self.duration
        mode = "idle"
        if self.state in (SKIP_ACTIVE, SKIP_SETTLING):
            mode = "skip"
        elif self.state in (PAUSE_PENDING, SCRUB_ACTIVE, CANCEL_WAIT_PAUSE):
            mode = "scrub"
        elif self.state == COMMITTING:
            mode = "commit"
        elif self.state == RESUME_PENDING:
            mode = "resume"
        return {
            "active": self.active,
            "generation": self.generation,
            "state": self.state,
            "mode": mode,
            "source": self.source,
            "target": self.target,
            "target_seconds": int(round(self.target)),
            "percent": clamp(percent, 0.0, 100.0),
            "time": format_time(self.target),
            "delta": format_delta(self.target - self.origin),
            "confirm": self.state == SCRUB_ACTIVE,
            "modal": self.manual,
            "controller_paused": self.controller_paused,
            "was_playing": self.was_playing,
            "playback_epoch": self.epoch if self.epoch is not None else "",
            "hold": self.hold_active,
            "hold_released": self.hold_released,
        }

    def _publish(self):
        self.publisher.publish(self.snapshot())
