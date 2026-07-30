from __future__ import absolute_import, division, print_function

import unittest

from seek_controller import (
    CANCEL_WAIT_PAUSE,
    COMMITTING,
    IDLE,
    PAUSE_PENDING,
    RESUME_PENDING,
    SCRUB_ACTIVE,
    SKIP_ACTIVE,
    SKIP_SETTLING,
    RepeatGuard,
    SeekController,
    format_delta,
    format_time,
    hold_velocity,
)


class FakePlayer(object):
    def __init__(self, current=100.0, duration=3600.0, paused=False):
        self.current = float(current)
        self.duration = float(duration)
        self.paused = bool(paused)
        self.seekable = True
        self.identity = "item-one"
        self.epoch = 1
        self.pause_requests = []
        self.resume_requests = []
        self.seek_requests = []
        self.retired = []

    def snapshot(self):
        return {
            "seekable": self.seekable,
            "current": self.current,
            "duration": self.duration,
            "paused": self.paused,
            "identity": self.identity,
            "epoch": self.epoch,
        }

    def request_pause(self, operation, identity, epoch):
        self.pause_requests.append((operation, identity, epoch))
        return True

    def request_resume(self, operation, identity, epoch):
        self.resume_requests.append((operation, identity, epoch))
        return True

    def request_seek(self, seconds, operation, identity, epoch):
        self.seek_requests.append(
            (float(seconds), operation, identity, epoch)
        )
        return True

    def retire_operation(self, operation):
        self.retired.append(operation)

    def retire_operations(self, operations):
        self.retired.extend(operations)


class FakePublisher(object):
    def __init__(self):
        self.snapshots = []
        self.clears = 0

    def publish(self, snapshot):
        self.snapshots.append(dict(snapshot))

    def clear(self):
        self.clears += 1


class ControllerHarness(object):
    def __init__(self, **player_kwargs):
        self.player = FakePlayer(**player_kwargs)
        self.publisher = FakePublisher()
        self.controller = SeekController(self.player, self.publisher)

    def ack_pause(self, now=0.0, matching=True):
        operation = (
            self.player.pause_requests[-1][0]
            if matching and self.player.pause_requests
            else None
        )
        self.player.paused = True
        self.controller.on_player_event(
            "paused",
            {
                "operation": operation,
                "identity": self.player.identity,
                "epoch": self.player.epoch,
            },
            now,
        )

    def ack_seek(self, index=-1, now=0.0):
        target, operation, _identity, _epoch = self.player.seek_requests[index]
        self.player.current = target
        self.controller.on_player_event(
            "seeked",
            {
                "operation": operation,
                "identity": self.player.identity,
                "epoch": self.player.epoch,
            },
            now,
        )

    def ack_resume(self, now=0.0):
        operation = self.player.resume_requests[-1][0]
        self.player.paused = False
        self.controller.on_player_event(
            "resumed",
            {
                "operation": operation,
                "identity": self.player.identity,
                "epoch": self.player.epoch,
            },
            now,
        )

    def start_timeline_hold(self, direction=1):
        for timestamp in (0.0, 0.40, 0.508, 0.616):
            self.controller.timeline_step(direction, timestamp)


class SeekControllerTest(unittest.TestCase):
    def test_formatting_and_gradual_velocity(self):
        self.assertEqual(format_time(3723), "1:02:03")
        self.assertEqual(format_time(754), "12:34")
        self.assertEqual(format_delta(-10), "\N{MINUS SIGN}0:10")
        self.assertEqual(format_delta(80), "+1:20")
        speeds = [hold_velocity(t, 3600) for t in (0, 1, 2, 4, 10)]
        self.assertEqual(speeds[0], 10)
        self.assertEqual(speeds, sorted(speeds))
        self.assertLess(speeds[1], 20)
        self.assertLessEqual(speeds[-1], 360)

    def test_one_hidden_tap_is_optimistic_then_one_absolute_seek(self):
        h = ControllerHarness()
        self.assertTrue(h.controller.hidden_step(1, 0.0))
        self.assertEqual(h.controller.state, SKIP_ACTIVE)
        self.assertEqual(h.controller.snapshot()["target_seconds"], 110)
        self.assertEqual(h.player.seek_requests, [])

        h.controller.tick(0.549)
        self.assertEqual(h.player.seek_requests, [])
        h.controller.tick(0.55)
        self.assertEqual(len(h.player.seek_requests), 1)
        self.assertEqual(h.player.seek_requests[0][0], 110)
        self.assertEqual(h.player.seek_requests[0][2:], ("item-one", 1))
        self.assertEqual(h.controller.state, SKIP_SETTLING)
        self.assertFalse(h.player.paused)

        h.ack_seek(now=0.7)
        self.assertEqual(h.controller.state, IDLE)

    def test_rapid_discrete_taps_never_accelerate_and_coalesce_once(self):
        h = ControllerHarness()
        for timestamp in (0.0, 0.10, 0.20, 0.30):
            h.controller.hidden_step(1, timestamp)
        self.assertEqual(h.controller.snapshot()["target_seconds"], 140)
        self.assertFalse(h.controller.hold_active)
        self.assertEqual(h.player.pause_requests, [])
        self.assertEqual(h.player.seek_requests, [])

        h.controller.tick(0.849)
        self.assertEqual(h.player.seek_requests, [])
        h.controller.tick(0.85)
        self.assertEqual([item[0] for item in h.player.seek_requests], [140])

    def test_brief_pause_does_not_lose_or_accelerate_a_tap(self):
        h = ControllerHarness()
        h.controller.hidden_step(1, 0.0)
        h.controller.hidden_step(1, 0.40)
        self.assertEqual(h.controller.snapshot()["target_seconds"], 110)
        self.assertEqual(h.controller.probe_count, 1)

        h.controller.tick(0.579)
        self.assertEqual(h.controller.snapshot()["target_seconds"], 110)
        h.controller.tick(0.581)
        self.assertEqual(h.controller.snapshot()["target_seconds"], 120)
        self.assertFalse(h.controller.hold_active)
        h.controller.tick(0.951)
        self.assertEqual([item[0] for item in h.player.seek_requests], [120])

    def test_failed_hold_probe_materializes_every_event(self):
        h = ControllerHarness()
        for timestamp in (0.0, 0.40, 0.508, 0.80):
            h.controller.hidden_step(1, timestamp)
        # The .40 and .508 candidates are materialized when the signature
        # breaks, and .80 begins a new candidate.
        self.assertEqual(h.controller.snapshot()["target_seconds"], 130)
        h.controller.tick(0.99)
        self.assertEqual(h.controller.snapshot()["target_seconds"], 140)
        self.assertFalse(h.controller.hold_active)

    def test_proven_hold_buffers_probe_without_target_rollback(self):
        h = ControllerHarness()
        visible_targets = []
        for timestamp in (0.0, 0.40, 0.508, 0.616):
            h.controller.hidden_step(1, timestamp)
            visible_targets.append(h.controller.target)
        self.assertEqual(visible_targets, [110, 110, 110, 110])
        self.assertEqual(h.controller.state, PAUSE_PENDING)
        self.assertTrue(h.controller.manual)
        self.assertTrue(h.controller.snapshot()["modal"])
        self.assertEqual(len(h.player.pause_requests), 1)
        self.assertEqual(h.player.seek_requests, [])

        h.ack_pause(0.65)
        self.assertEqual(h.controller.state, SCRUB_ACTIVE)
        self.assertTrue(h.controller.controller_paused)
        h.controller.tick(0.90)
        frozen = h.controller.target
        h.controller.tick(2.0)
        self.assertAlmostEqual(h.controller.target, frozen)
        self.assertTrue(h.controller.hold_released)

    def test_hold_direction_reversal_is_one_fine_step_and_resets_ramp(self):
        h = ControllerHarness()
        for timestamp in (0.0, 0.40, 0.508, 0.616):
            h.controller.hidden_step(1, timestamp)
        h.ack_pause(0.65)
        h.controller.tick(0.72)
        before = h.controller.target
        h.controller.hidden_step(-1, 0.73)
        self.assertLess(h.controller.target, before)
        self.assertAlmostEqual(h.controller.target, before - 10.0, delta=0.2)
        self.assertEqual(h.controller.gesture_direction, -1)
        self.assertEqual(h.controller.hold_started, 0.73)

    def test_press_after_released_hold_is_a_new_ten_second_gesture(self):
        h = ControllerHarness()
        for timestamp in (0.0, 0.40, 0.508, 0.616):
            h.controller.hidden_step(1, timestamp)
        h.ack_pause(0.65)
        h.controller.tick(0.90)
        self.assertTrue(h.controller.hold_released)
        before = h.controller.target

        h.controller.hidden_step(1, 3.0)
        self.assertAlmostEqual(h.controller.target, before + 10.0)
        self.assertFalse(h.controller.hold_active)
        h.controller.tick(3.05)
        self.assertAlmostEqual(h.controller.target, before + 10.0)

    def test_isolated_timeline_tap_auto_commits_without_pause(self):
        h = ControllerHarness()
        h.controller.timeline_step(1, 0.0)
        self.assertEqual(h.controller.target, 110)
        self.assertEqual(h.controller.state, SKIP_ACTIVE)
        self.assertEqual(h.player.pause_requests, [])
        self.assertEqual(h.player.seek_requests, [])
        h.controller.tick(0.55)
        self.assertEqual([item[0] for item in h.player.seek_requests], [110])
        self.assertEqual(h.controller.state, SKIP_SETTLING)

    def test_hidden_commit_then_separated_timeline_tap_is_another_skip(self):
        h = ControllerHarness()
        h.controller.hidden_step(1, 0.0)
        h.controller.tick(0.55)
        h.ack_seek(now=0.70)
        self.assertEqual(h.player.current, 110)

        h.controller.timeline_step(1, 0.90)
        self.assertEqual(h.controller.state, SKIP_ACTIVE)
        self.assertEqual(h.controller.target, 120)
        self.assertEqual(h.player.pause_requests, [])
        h.controller.tick(1.451)
        self.assertEqual(
            [item[0] for item in h.player.seek_requests],
            [110, 120],
        )

    def test_timeline_taps_with_brief_pause_remain_exact_slow_skips(self):
        h = ControllerHarness()
        h.controller.timeline_step(1, 0.0)
        h.controller.timeline_step(1, 0.40)
        self.assertEqual(h.controller.target, 110)
        h.controller.tick(0.581)
        self.assertEqual(h.controller.target, 120)
        self.assertEqual(h.player.pause_requests, [])
        h.controller.tick(0.951)
        self.assertEqual([item[0] for item in h.player.seek_requests], [120])

    def test_timeline_only_proven_hold_pauses_then_commits_and_resumes(self):
        h = ControllerHarness()
        h.start_timeline_hold(1)
        self.assertEqual(h.controller.target, 110)
        self.assertEqual(h.controller.state, PAUSE_PENDING)
        self.assertEqual(h.player.pause_requests[0][1:], ("item-one", 1))
        self.assertEqual(h.player.seek_requests, [])
        h.ack_pause(0.65)
        self.assertEqual(h.controller.state, SCRUB_ACTIVE)

        h.controller.confirm(0.70)
        self.assertEqual(h.controller.state, COMMITTING)
        self.assertEqual(
            [item[0] for item in h.player.seek_requests],
            [h.controller.target],
        )
        h.ack_seek(now=0.80)
        self.assertEqual(h.controller.state, RESUME_PENDING)
        self.assertEqual(len(h.player.resume_requests), 1)
        self.assertEqual(h.player.resume_requests[0][1:], ("item-one", 1))
        h.ack_resume(0.90)
        self.assertEqual(h.controller.state, IDLE)

    def test_ok_during_pause_pending_queues_commit(self):
        h = ControllerHarness()
        h.start_timeline_hold(-1)
        self.assertTrue(h.controller.confirm(0.62))
        self.assertEqual(h.player.seek_requests, [])
        h.ack_pause(0.65)
        self.assertEqual(h.controller.state, COMMITTING)
        self.assertEqual([item[0] for item in h.player.seek_requests], [90])

    def test_user_paused_content_remains_paused_after_commit(self):
        h = ControllerHarness(paused=True)
        h.start_timeline_hold(1)
        self.assertEqual(h.controller.state, SCRUB_ACTIVE)
        self.assertEqual(h.player.pause_requests, [])
        h.controller.confirm(0.70)
        h.ack_seek(now=0.80)
        self.assertEqual(h.controller.state, IDLE)
        self.assertTrue(h.player.paused)
        self.assertEqual(h.player.resume_requests, [])

    def test_cancel_owned_scrub_resumes_without_seeking(self):
        h = ControllerHarness()
        h.start_timeline_hold(1)
        h.ack_pause(0.65)
        h.controller.cancel(0.70)
        self.assertEqual(h.controller.state, RESUME_PENDING)
        self.assertEqual(h.player.seek_requests, [])
        self.assertEqual(len(h.player.resume_requests), 1)

    def test_cancel_before_matching_pause_resumes_late_pause(self):
        h = ControllerHarness()
        h.start_timeline_hold(1)
        h.controller.cancel(0.62)
        self.assertEqual(h.controller.state, CANCEL_WAIT_PAUSE)
        h.ack_pause(0.70)
        self.assertEqual(h.controller.state, RESUME_PENDING)
        self.assertEqual(len(h.player.resume_requests), 1)

    def test_cancel_before_external_pause_never_toggles_user_state(self):
        h = ControllerHarness()
        h.start_timeline_hold(1)
        h.controller.cancel(0.62)
        h.ack_pause(0.70, matching=False)
        self.assertEqual(h.controller.state, CANCEL_WAIT_PAUSE)
        self.assertEqual(h.player.resume_requests, [])

    def test_missing_pause_callback_is_retired_and_owned_pause_resumed(self):
        h = ControllerHarness()
        h.start_timeline_hold(1)
        pause_operation = h.player.pause_requests[-1][0]
        # Kodi applied the validated toggle, but its callback was lost.
        h.player.paused = True
        h.controller.tick(1.366)
        self.assertIn(pause_operation, h.player.retired)
        self.assertEqual(h.controller.state, RESUME_PENDING)
        self.assertEqual(len(h.player.resume_requests), 1)

    def test_untagged_and_wrong_seek_callbacks_never_commit(self):
        h = ControllerHarness()
        h.start_timeline_hold(1)
        h.ack_pause(0.65)
        h.controller.confirm(0.70)
        expected = h.controller.pending_operation
        h.controller.on_player_event(
            "seeked",
            {
                "operation": None,
                "identity": h.player.identity,
                "epoch": h.player.epoch,
            },
            0.80,
        )
        self.assertEqual(h.controller.state, COMMITTING)
        h.controller.on_player_event(
            "seeked",
            {
                "operation": "old-seek",
                "identity": h.player.identity,
                "epoch": h.player.epoch,
            },
            0.90,
        )
        self.assertEqual(h.controller.state, COMMITTING)
        h.controller.on_player_event(
            "seeked",
            {
                "operation": expected,
                "identity": h.player.identity,
                "epoch": h.player.epoch,
            },
            1.00,
        )
        self.assertEqual(h.controller.state, RESUME_PENDING)

    def test_wrong_resume_callback_does_not_complete_transaction(self):
        h = ControllerHarness()
        h.start_timeline_hold(1)
        h.ack_pause(0.65)
        h.controller.cancel(0.70)
        expected = h.controller.pending_operation
        h.controller.on_player_event(
            "resumed",
            {
                "operation": None,
                "identity": h.player.identity,
                "epoch": h.player.epoch,
            },
            0.80,
        )
        self.assertEqual(h.controller.state, RESUME_PENDING)
        h.controller.on_player_event(
            "resumed",
            {
                "operation": expected,
                "identity": h.player.identity,
                "epoch": h.player.epoch,
            },
            0.90,
        )
        self.assertEqual(h.controller.state, IDLE)

    def test_reset_and_timeouts_retire_outstanding_intents(self):
        h = ControllerHarness()
        h.start_timeline_hold(1)
        operation = h.player.pause_requests[-1][0]
        h.controller.reset()
        self.assertIn(operation, h.player.retired)

        h = ControllerHarness()
        h.controller.hidden_step(1, 0.0)
        h.controller.tick(0.55)
        operation = h.player.seek_requests[-1][1]
        h.controller.tick(4.55)
        self.assertIn(operation, h.player.retired)
        handoff = h.controller.snapshot()
        self.assertTrue(handoff["handoff_active"])
        self.assertEqual(handoff["handoff_target"], 110)

    def test_stale_media_callback_cannot_abandon_controller_owned_pause(self):
        h = ControllerHarness()
        h.start_timeline_hold(1)
        h.ack_pause(0.65)
        self.assertEqual(h.controller.state, SCRUB_ACTIVE)
        self.assertTrue(h.controller.controller_paused)

        h.controller.on_player_event(
            "seeked",
            {
                "operation": "old-operation",
                "identity": "old-item",
                "epoch": 0,
            },
            0.70,
        )
        self.assertEqual(h.controller.state, SCRUB_ACTIVE)
        self.assertTrue(h.controller.controller_paused)
        self.assertEqual(h.player.resume_requests, [])

    def test_stale_same_media_callbacks_cannot_abandon_owned_pause(self):
        h = ControllerHarness()
        h.start_timeline_hold(1)
        h.ack_pause(0.65)

        for kind in ("paused", "resumed"):
            h.controller.on_player_event(
                kind,
                {
                    "operation": "old-operation",
                    "identity": h.player.identity,
                    "epoch": h.player.epoch,
                },
                0.70,
            )
            self.assertEqual(h.controller.state, SCRUB_ACTIVE)
            self.assertTrue(h.controller.controller_paused)

        h.controller.cancel(0.80)
        self.assertEqual(h.controller.state, RESUME_PENDING)
        self.assertEqual(len(h.player.resume_requests), 1)

    def test_new_gesture_uses_logical_target_until_raw_clock_converges(self):
        h = ControllerHarness()
        h.controller.hidden_step(1, 0.0)
        h.controller.tick(0.55)
        h.ack_seek(now=0.70)
        self.assertEqual(h.controller.snapshot()["handoff_target"], 110)

        # Kodi can report the pre-seek decoder clock for several samples.
        h.player.current = 100
        h.controller.hidden_step(1, 0.80)
        self.assertEqual(h.controller.target, 120)

    def test_chapter_browse_uses_logical_handoff_origin(self):
        h = ControllerHarness()
        h.controller.hidden_step(1, 0.0)
        h.controller.tick(0.55)
        h.ack_seek(now=0.70)
        h.player.current = 100

        self.assertTrue(h.controller.begin_chapter_browse(0.80))
        self.assertEqual(h.controller.origin, 110)
        self.assertEqual(h.controller.target, 110)

    def test_missing_commit_callback_still_hands_target_to_renderer(self):
        h = ControllerHarness(paused=True)
        h.start_timeline_hold(1)
        self.assertEqual(h.controller.state, SCRUB_ACTIVE)
        h.controller.confirm(0.70)
        target = h.controller.target
        self.assertEqual(h.controller.state, COMMITTING)

        h.controller.tick(4.70)
        snapshot = h.controller.snapshot()
        self.assertEqual(snapshot["state"], IDLE)
        self.assertTrue(snapshot["handoff_active"])
        self.assertEqual(snapshot["handoff_target"], target)

    def test_back_discards_uncommitted_skip_without_seeking(self):
        h = ControllerHarness()
        h.controller.hidden_step(1, 0.0)
        h.controller.cancel(0.1)
        self.assertEqual(h.controller.state, IDLE)
        self.assertEqual(h.player.seek_requests, [])

    def test_back_during_skip_settlement_never_queues_second_seek(self):
        h = ControllerHarness()
        h.controller.hidden_step(1, 0.0)
        h.controller.tick(0.55)
        operation = h.player.seek_requests[0][1]
        h.controller.cancel(0.60)
        self.assertEqual(h.controller.state, SKIP_SETTLING)
        self.assertTrue(h.controller.back_dismisses_osd)
        self.assertEqual(
            h.controller.snapshot()["skip_operation"],
            operation,
        )
        self.assertEqual(len(h.player.seek_requests), 1)
        self.assertNotIn(operation, h.player.retired)
        h.ack_seek(now=0.70)
        self.assertEqual(h.controller.state, IDLE)

    def test_back_abandons_new_gesture_but_keeps_issued_skip_attributed(self):
        h = ControllerHarness()
        h.controller.hidden_step(1, 0.0)
        h.controller.tick(0.55)
        operation = h.player.seek_requests[0][1]
        h.controller.hidden_step(1, 0.60)
        self.assertEqual(h.controller.state, SKIP_ACTIVE)
        self.assertEqual(h.controller.target, 120)
        self.assertTrue(h.controller.back_dismisses_osd)

        h.controller.cancel(0.70)
        self.assertEqual(h.controller.state, SKIP_SETTLING)
        self.assertEqual(h.controller.target, 110)
        self.assertEqual(h.controller.skip_inflight, operation)
        self.assertNotIn(operation, h.player.retired)
        h.ack_seek(now=0.80)
        self.assertEqual(h.controller.state, IDLE)

    def test_cancelled_hold_keeps_older_issued_skip_attributed(self):
        h = ControllerHarness()
        h.controller.hidden_step(1, 0.0)
        h.controller.tick(0.55)
        skip_operation = h.player.seek_requests[0][1]
        for timestamp in (0.60, 1.00, 1.108, 1.216):
            h.controller.timeline_step(1, timestamp)
        h.ack_pause(1.25)
        self.assertEqual(h.controller.state, SCRUB_ACTIVE)

        h.controller.cancel(1.30)
        self.assertEqual(h.controller.state, RESUME_PENDING)
        h.ack_resume(1.40)
        self.assertEqual(h.controller.state, SKIP_SETTLING)
        self.assertEqual(h.controller.skip_inflight, skip_operation)
        self.assertNotIn(skip_operation, h.player.retired)
        h.ack_seek(index=0, now=1.50)
        self.assertEqual(h.controller.state, IDLE)

    def test_item_or_epoch_change_clears_conservatively(self):
        for attribute, value in (("identity", "item-two"), ("epoch", 2)):
            h = ControllerHarness()
            h.controller.timeline_step(1, 0.0)
            setattr(h.player, attribute, value)
            h.controller.tick(0.1)
            self.assertEqual(h.controller.state, IDLE)
            self.assertEqual(h.player.seek_requests, [])
            self.assertEqual(h.player.resume_requests, [])

    def test_transient_nonseekable_snapshot_cannot_make_stale_event_reset(self):
        h = ControllerHarness()
        h.start_timeline_hold(1)
        h.ack_pause(0.65)
        h.player.seekable = False
        h.controller.on_player_event(
            "seeked",
            {
                "operation": "old-operation",
                "identity": "old-item",
                "epoch": 0,
            },
            0.70,
        )
        self.assertEqual(h.controller.state, SCRUB_ACTIVE)
        self.assertTrue(h.controller.controller_paused)
        self.assertEqual(h.player.resume_requests, [])

    def test_empty_identity_cannot_start_transaction(self):
        h = ControllerHarness()
        h.player.identity = ""
        self.assertFalse(h.controller.timeline_step(1, 0.0))
        self.assertFalse(h.controller.hidden_step(1, 0.0))
        self.assertEqual(h.controller.state, IDLE)
        self.assertEqual(h.player.pause_requests, [])
        self.assertEqual(h.player.seek_requests, [])

    def test_chapter_browse_is_pause_owned_zero_delta_transaction(self):
        h = ControllerHarness()
        self.assertTrue(h.controller.begin_chapter_browse(0.0))
        self.assertEqual(h.controller.source, "chapter")
        self.assertEqual(h.controller.target, 100)
        h.controller.set_target(600)
        self.assertEqual(h.controller.target, 600)
        h.controller.confirm(0.02)
        h.ack_pause(0.1)
        self.assertEqual([item[0] for item in h.player.seek_requests], [600])

    def test_delayed_skip_callback_serializes_later_timeline_skip(self):
        h = ControllerHarness()
        h.controller.hidden_step(1, 0.0)
        h.controller.tick(0.55)
        self.assertEqual([item[0] for item in h.player.seek_requests], [110])

        h.controller.timeline_step(1, 0.60)
        self.assertEqual(h.controller.target, 120)
        self.assertEqual(h.controller.state, SKIP_ACTIVE)
        self.assertEqual(h.player.pause_requests, [])
        h.controller.tick(1.151)
        self.assertEqual([item[0] for item in h.player.seek_requests], [110])

        h.ack_seek(index=0, now=1.20)
        self.assertEqual([item[0] for item in h.player.seek_requests], [110, 120])
        self.assertEqual(h.controller.state, SKIP_SETTLING)

    def test_proven_timeline_hold_serializes_behind_inflight_skip(self):
        h = ControllerHarness()
        h.controller.hidden_step(1, 0.0)
        h.controller.tick(0.55)
        self.assertEqual([item[0] for item in h.player.seek_requests], [110])

        for timestamp in (0.60, 1.00, 1.108, 1.216):
            h.controller.timeline_step(1, timestamp)
        self.assertEqual(h.controller.state, PAUSE_PENDING)
        self.assertEqual(h.controller.target, 120)
        h.ack_pause(1.25)
        h.controller.confirm(1.30)
        self.assertEqual([item[0] for item in h.player.seek_requests], [110])

        h.ack_seek(index=0, now=1.35)
        self.assertEqual(len(h.player.seek_requests), 2)
        self.assertEqual(h.controller.state, COMMITTING)

    def test_new_gesture_revokes_stale_queued_flush_watermark(self):
        h = ControllerHarness()
        # A commits while its callback remains in flight.
        h.controller.hidden_step(1, 0.0)
        h.controller.tick(0.55)
        self.assertEqual([item[0] for item in h.player.seek_requests], [110])

        # B starts from A's logical target, then becomes quiet and requests a
        # deferred flush behind A.
        h.controller.timeline_step(1, 0.60)
        h.controller.tick(1.151)
        self.assertTrue(h.controller.skip_flush_requested)

        # C starts after that boundary. It must invalidate B's stale flush.
        h.controller.timeline_step(1, 1.20)
        self.assertFalse(h.controller.skip_flush_requested)
        h.ack_seek(index=0, now=1.21)
        self.assertEqual(h.controller.state, SKIP_ACTIVE)
        self.assertEqual(len(h.player.seek_requests), 1)

        # C's cadence remains intact after A's acknowledgement and can still
        # prove a hold instead of being reset into a new fixed-skip gesture.
        for timestamp in (1.60, 1.708, 1.816):
            h.controller.timeline_step(1, timestamp)
        self.assertEqual(h.controller.state, PAUSE_PENDING)
        self.assertEqual(h.controller.target, 130)
        self.assertEqual(len(h.player.pause_requests), 1)
        self.assertEqual(len(h.player.seek_requests), 1)

    def test_inflight_skip_timeout_does_not_strand_new_gesture(self):
        h = ControllerHarness()
        h.controller.hidden_step(1, 0.0)
        h.controller.tick(0.55)
        h.controller.hidden_step(1, 0.60)
        self.assertEqual(h.controller.state, SKIP_ACTIVE)
        h.controller.tick(4.56)
        # The old operation expires before SKIP_ACTIVE returns, allowing the
        # new logical target to issue its own single seek.
        self.assertEqual([item[0] for item in h.player.seek_requests], [110, 120])

    def test_inflight_timeout_keeps_anchor_behind_new_pause_gesture(self):
        h = ControllerHarness()
        h.controller.hidden_step(1, 0.0)
        h.controller.tick(0.55)
        for timestamp in (3.80, 4.20, 4.308, 4.416):
            h.controller.timeline_step(1, timestamp)
        self.assertEqual(h.controller.state, PAUSE_PENDING)

        h.controller.tick(4.56)
        self.assertEqual(h.controller.state, PAUSE_PENDING)
        self.assertTrue(h.controller.snapshot()["handoff_active"])
        self.assertEqual(h.controller.snapshot()["handoff_target"], 110)

        h.controller.cancel(4.60)
        h.controller.tick(6.61)
        self.assertEqual(h.controller.state, IDLE)
        h.controller.hidden_step(1, 6.70)
        self.assertEqual(h.controller.target, 120)

    def test_boundary_clamping_never_wraps_or_seeks_exact_duration(self):
        low = ControllerHarness(current=5, duration=100)
        low.controller.hidden_step(-1, 0.0)
        low.controller.hidden_step(-1, 0.1)
        self.assertEqual(low.controller.target, 0)
        low.controller.tick(0.65)
        self.assertEqual(low.player.seek_requests[0][0], 0)

        high = ControllerHarness(current=99.5, duration=100)
        high.controller.hidden_step(1, 0.0)
        self.assertEqual(high.controller.target, 99.5)


class RepeatGuardTest(unittest.TestCase):
    def test_repeat_train_requires_a_full_quiet_period(self):
        guard = RepeatGuard(quiet_period=0.50)
        self.assertTrue(guard.accept("select", 0.0))
        self.assertFalse(guard.accept("select", 0.10))
        self.assertFalse(guard.accept("select", 0.35))
        self.assertFalse(guard.accept("select", 0.70))
        self.assertTrue(guard.accept("select", 1.21))

    def test_actions_are_guarded_independently(self):
        guard = RepeatGuard(quiet_period=0.50)
        self.assertTrue(guard.accept("select", 0.0))
        self.assertTrue(guard.accept("back", 0.1))

    def test_modal_layer_can_arm_an_already_consumed_train(self):
        guard = RepeatGuard(quiet_period=0.50)
        guard.arm("select", 1.0)
        self.assertFalse(guard.accept("select", 1.1))
        self.assertTrue(guard.accept("back", 1.1))
        self.assertTrue(guard.accept("select", 1.61))


if __name__ == "__main__":
    unittest.main()
