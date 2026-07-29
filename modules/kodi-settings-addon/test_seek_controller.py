from __future__ import absolute_import, division, print_function

import unittest

from seek_controller import (
    RepeatGuard,
    SeekController,
    format_delta,
    format_time,
    hold_velocity,
)


class FakePlayer(object):
    def __init__(self, current=100.0, duration=3600.0, paused=False):
        self.current = current
        self.duration = duration
        self.paused = paused
        self.seekable = True
        self.identity = "item-one"
        self.seeks = []
        self.play_calls = 0

    def is_seekable(self):
        return self.seekable

    def get_time(self):
        return self.current

    def get_duration(self):
        return self.duration

    def is_paused(self):
        return self.paused

    def get_identity(self):
        return self.identity

    def seek(self, seconds):
        self.seeks.append(seconds)

    def ensure_playing(self):
        if self.paused:
            self.paused = False
            self.play_calls += 1


class FakePublisher(object):
    def __init__(self):
        self.snapshots = []
        self.clears = 0

    def publish(self, snapshot):
        self.snapshots.append(dict(snapshot))

    def clear(self):
        self.clears += 1


class SeekControllerTest(unittest.TestCase):
    def make_controller(self, **player_kwargs):
        player = FakePlayer(**player_kwargs)
        publisher = FakePublisher()
        return player, publisher, SeekController(player, publisher)

    def test_formatting_and_velocity(self):
        self.assertEqual(format_time(3723), "1:02:03")
        self.assertEqual(format_time(754), "12:34")
        self.assertEqual(format_delta(-10), "\N{MINUS SIGN}0:10")
        self.assertEqual(format_delta(80), "+1:20")
        self.assertAlmostEqual(hold_velocity(0, 3600), 10)
        self.assertAlmostEqual(hold_velocity(1.25, 3600), 20)
        self.assertLessEqual(hold_velocity(20, 3600), 360)

    def test_one_tap_publishes_target_then_commits_once(self):
        player, publisher, controller = self.make_controller()
        controller.arrow(-1, now=0)
        self.assertEqual(controller.snapshot()["target_seconds"], 90)
        self.assertEqual(player.seeks, [])

        controller.tick(0.56)
        self.assertEqual(player.seeks, [90])
        self.assertEqual(controller.state, "settling")
        self.assertFalse(player.paused)

        controller.tick(0.80)
        self.assertEqual(player.seeks, [90])
        player.current = 90
        controller.tick(0.92)
        self.assertEqual(controller.state, "idle")
        self.assertGreaterEqual(publisher.clears, 2)

    def test_rapid_human_taps_are_exact_fixed_steps(self):
        player, _publisher, controller = self.make_controller()
        controller.arrow(-1, now=0)
        controller.arrow(-1, now=0.18)
        controller.arrow(-1, now=0.36)
        self.assertEqual(controller.snapshot()["target_seconds"], 70)
        controller.tick(0.92)
        self.assertEqual(player.seeks, [70])

    def test_slow_ambiguous_events_resolve_as_taps(self):
        player, _publisher, controller = self.make_controller()
        controller.arrow(-1, now=0)
        controller.arrow(-1, now=0.43)
        self.assertEqual(controller.snapshot()["target_seconds"], 90)
        controller.tick(0.62)
        self.assertEqual(controller.snapshot()["target_seconds"], 80)
        controller.arrow(-1, now=0.86)
        controller.tick(1.05)
        self.assertEqual(controller.snapshot()["target_seconds"], 70)
        controller.tick(1.42)
        self.assertEqual(player.seeks, [70])

    def test_measured_repeat_signature_becomes_confirmable_hold(self):
        player, _publisher, controller = self.make_controller()
        controller.arrow(1, now=0)
        controller.arrow(1, now=0.40)
        controller.arrow(1, now=0.508)
        controller.arrow(1, now=0.616)
        self.assertEqual(controller.state, "hold")
        self.assertEqual(player.seeks, [])

        controller.tick(0.90)
        self.assertEqual(controller.state, "hold-pending")
        self.assertGreater(controller.target, 110)
        self.assertEqual(player.seeks, [])

        target = controller.target
        self.assertTrue(controller.confirm(now=1.0))
        self.assertEqual(player.seeks, [target])
        self.assertEqual(controller.state, "settling")

    def test_single_ambiguous_repeat_is_a_second_tap(self):
        player, _publisher, controller = self.make_controller()
        controller.arrow(1, now=0)
        controller.arrow(1, now=0.40)
        controller.tick(0.59)
        self.assertEqual(controller.snapshot()["target_seconds"], 120)
        controller.tick(0.96)
        self.assertEqual(player.seeks, [120])

    def test_irregular_hold_onset_still_requires_dense_repeat_pattern(self):
        player, _publisher, controller = self.make_controller()
        for timestamp in (0, 0.40, 0.564, 0.765, 0.865, 0.965, 1.065):
            controller.arrow(1, now=timestamp)
        self.assertEqual(controller.state, "hold")
        self.assertEqual(player.seeks, [])
        controller.tick(1.31)
        self.assertEqual(controller.state, "hold-pending")
        self.assertEqual(player.seeks, [])

    def test_dense_repeat_fails_safe_to_hold_without_large_skip(self):
        player, _publisher, controller = self.make_controller()
        controller.arrow(1, now=0)
        previous_target = controller.target
        for timestamp in (0.14, 0.28, 0.42, 0.56):
            controller.arrow(1, now=timestamp)
            self.assertGreaterEqual(controller.target, previous_target)
            previous_target = controller.target
        self.assertEqual(controller.state, "hold")
        self.assertLess(controller.target, 120)
        self.assertEqual(player.seeks, [])

    def test_fast_measured_human_taps_remain_exact_steps(self):
        player, _publisher, controller = self.make_controller()
        for timestamp in (0, 0.166, 0.332, 0.498, 0.664):
            controller.arrow(1, now=timestamp)
        self.assertEqual(controller.state, "tap")
        self.assertEqual(controller.snapshot()["target_seconds"], 150)
        controller.tick(1.215)
        self.assertEqual(player.seeks, [150])

    def test_timeline_never_auto_commits_and_back_cancels(self):
        player, _publisher, controller = self.make_controller()
        controller.arrow(1, source="timeline", now=0)
        controller.arrow(1, source="timeline", now=0.20)
        controller.tick(20)
        self.assertEqual(controller.state, "timeline")
        self.assertEqual(controller.snapshot()["target_seconds"], 120)
        self.assertEqual(player.seeks, [])
        self.assertTrue(controller.cancel())
        self.assertEqual(player.seeks, [])

    def test_timeline_hold_uses_buffered_classifier_without_jumping(self):
        player, _publisher, controller = self.make_controller()
        for timestamp in (0, 0.40, 0.508, 0.616):
            controller.arrow(1, source="timeline", now=timestamp)
        self.assertEqual(controller.state, "hold")
        self.assertEqual(controller.snapshot()["target_seconds"], 110)
        self.assertEqual(player.seeks, [])

    def test_timeline_confirm_plays_after_one_seek(self):
        player, _publisher, controller = self.make_controller(paused=True)
        controller.arrow(-1, source="timeline", now=0)
        controller.arrow(-1, source="timeline", now=0.2)
        controller.confirm(now=0.3)
        self.assertEqual(player.seeks, [80])
        self.assertEqual(player.play_calls, 1)
        self.assertFalse(player.paused)

    def test_hold_reversal_preserves_target_and_resets_velocity(self):
        player, _publisher, controller = self.make_controller()
        controller.arrow(1, now=0)
        controller.arrow(1, now=0.40)
        controller.arrow(1, now=0.505)
        controller.arrow(1, now=0.610)
        controller.tick(0.70)
        controller.tick(0.71)
        before = controller.target
        controller.arrow(-1, now=0.71)
        self.assertAlmostEqual(controller.target, before - 10, places=5)
        self.assertEqual(controller.last_direction, -1)
        self.assertEqual(controller.hold_started, 0.71)

    def test_new_tap_during_settlement_uses_logical_target(self):
        player, _publisher, controller = self.make_controller()
        controller.arrow(1, now=0)
        controller.tick(0.56)
        self.assertEqual(player.seeks, [110])
        self.assertEqual(player.current, 100)
        controller.arrow(1, now=0.60)
        self.assertEqual(controller.snapshot()["target_seconds"], 120)
        controller.tick(1.16)
        self.assertEqual(player.seeks, [110, 120])

    def test_paused_tap_preserves_pause(self):
        player, _publisher, controller = self.make_controller(paused=True)
        controller.arrow(-1, now=0)
        controller.tick(0.56)
        self.assertEqual(player.seeks, [90])
        self.assertTrue(player.paused)
        self.assertEqual(player.play_calls, 0)

    def test_boundaries_clamp_without_wrapping(self):
        player, _publisher, controller = self.make_controller(
            current=5, duration=100
        )
        controller.arrow(-1, now=0)
        controller.arrow(-1, now=0.20)
        self.assertEqual(controller.snapshot()["target_seconds"], 0)
        controller.tick(0.76)
        self.assertEqual(player.seeks, [0])

    def test_end_boundary_never_seeks_exact_duration_or_backwards(self):
        player, _publisher, controller = self.make_controller(
            current=99.5, duration=100
        )
        controller.arrow(1, now=0)
        self.assertEqual(controller.target, 99.5)
        controller.tick(0.56)
        self.assertEqual(player.seeks, [99.5])

    def test_playback_loss_clears_without_seek(self):
        player, _publisher, controller = self.make_controller()
        controller.arrow(1, now=0)
        player.seekable = False
        controller.tick(0.2)
        self.assertEqual(controller.state, "idle")
        self.assertEqual(player.seeks, [])

    def test_item_change_clears_without_seeking_new_video(self):
        player, _publisher, controller = self.make_controller()
        controller.arrow(1, now=0)
        player.identity = "item-two"
        controller.tick(0.2)
        self.assertEqual(controller.state, "idle")
        self.assertEqual(player.seeks, [])

    def test_commit_revalidates_identity_before_touching_player(self):
        player, _publisher, controller = self.make_controller()
        controller.arrow(1, now=0)
        player.identity = "item-two"
        self.assertFalse(controller.commit(play_after=False, now=0.1))
        self.assertEqual(controller.state, "idle")
        self.assertEqual(player.seeks, [])


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


if __name__ == "__main__":
    unittest.main()
