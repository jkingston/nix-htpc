from __future__ import absolute_import, division, print_function

import unittest

from input_router import InputRouter, KodiCommands
from seek_controller import (
    COMMITTING,
    IDLE,
    PAUSE_PENDING,
    RESUME_PENDING,
    SCRUB_ACTIVE,
    SKIP_ACTIVE,
    SKIP_SETTLING,
    SeekController,
)


class FakePlayerBoundary(object):
    def __init__(self):
        self.current = 100.0
        self.duration = 3600.0
        self.paused = False
        self.identity = "item-one"
        self.epoch = 1
        self.pause_requests = []
        self.seek_requests = []
        self.resume_requests = []
        self.retired_operations = []

    def snapshot(self):
        return {
            "seekable": True,
            "current": self.current,
            "duration": self.duration,
            "paused": self.paused,
            "identity": self.identity,
            "epoch": self.epoch,
        }

    def request_pause(self, operation, identity, epoch):
        self.pause_requests.append((operation, identity, epoch))
        return True

    def request_seek(self, seconds, operation, identity, epoch):
        self.seek_requests.append(
            (float(seconds), operation, identity, epoch)
        )
        return True

    def request_resume(self, operation, identity, epoch):
        self.resume_requests.append((operation, identity, epoch))
        return True

    def retire_operation(self, operation):
        self.retired_operations.append(operation)

    def retire_operations(self, operations):
        self.retired_operations.extend(operations)


class FakePresenterBoundary(object):
    def __init__(self):
        self.calls = []

    def emphasize_timeline(self):
        self.calls.append("emphasize-timeline")

    def focus_transport(self):
        self.calls.append("focus-transport")


class FakePublisherBoundary(object):
    def __init__(self):
        self.snapshots = []
        self.clear_count = 0

    def publish(self, snapshot):
        self.snapshots.append(dict(snapshot))

    def clear(self):
        self.clear_count += 1


class FakeChapterBoundary(object):
    is_open = False

    @staticmethod
    def tick(_timestamp):
        return None


class PlaybackOsdHarness(object):
    def __init__(self):
        self.now = [0.0]
        self.player = FakePlayerBoundary()
        self.publisher = FakePublisherBoundary()
        self.presenter = FakePresenterBoundary()
        self.chapters = FakeChapterBoundary()
        self.builtins = []
        self.controller = SeekController(
            self.player,
            self.publisher,
            clock=lambda: self.now[0],
        )
        self.router = InputRouter(
            self.controller,
            self.player,
            self.presenter,
            self.chapters,
            KodiCommands(self.builtins.append),
            clock=lambda: self.now[0],
        )

    def route(self, action, timestamp):
        self.now[0] = float(timestamp)
        if not self.router.handle(action, timestamp):
            raise AssertionError("router rejected %s" % action)

    def ack_pause(self, timestamp):
        operation, identity, epoch = self.player.pause_requests[-1]
        self.player.paused = True
        self.controller.on_player_event(
            "paused",
            {
                "operation": operation,
                "identity": identity,
                "epoch": epoch,
            },
            timestamp,
        )

    def ack_seek(self, timestamp):
        target, operation, identity, epoch = self.player.seek_requests[-1]
        self.player.current = target
        self.controller.on_player_event(
            "seeked",
            {
                "operation": operation,
                "identity": identity,
                "epoch": epoch,
            },
            timestamp,
        )

    def ack_resume(self, timestamp):
        operation, identity, epoch = self.player.resume_requests[-1]
        self.player.paused = False
        self.controller.on_player_event(
            "resumed",
            {
                "operation": operation,
                "identity": identity,
                "epoch": epoch,
            },
            timestamp,
        )

    def promote_right_hold(self, timeline_from=0):
        observations = []
        events = [("right", 0.0)]
        for repeat, timestamp in enumerate((0.400, 0.508, 0.616)):
            action = (
                "timeline-right"
                if repeat >= timeline_from
                else "right"
            )
            events.append((action, timestamp))
        for action, timestamp in events:
            self.route(action, timestamp)
            observations.append(
                (
                    self.controller.target,
                    self.controller.state,
                    self.controller.source,
                    self.controller.probe_count,
                    self.controller.generation,
                )
            )
        return observations


class PlaybackOsdBehaviorTest(unittest.TestCase):
    def test_hidden_right_continues_on_timeline_as_one_hold(self):
        harness = PlaybackOsdHarness()

        observations = harness.promote_right_hold()

        self.assertEqual(
            observations,
            [
                (110.0, SKIP_ACTIVE, "fullscreen", 0, 1),
                (110.0, SKIP_ACTIVE, "timeline", 1, 1),
                (110.0, SKIP_ACTIVE, "timeline", 2, 1),
                (110.0, PAUSE_PENDING, "hold", 0, 1),
            ],
        )
        self.assertEqual(harness.controller.state, PAUSE_PENDING)
        self.assertTrue(harness.controller.manual)
        self.assertTrue(harness.controller.hold_active)
        self.assertEqual(harness.controller.source, "hold")
        self.assertTrue(harness.controller.was_playing)
        self.assertEqual(
            harness.player.pause_requests,
            [(harness.controller.pending_operation, "item-one", 1)],
        )
        self.assertEqual(harness.player.seek_requests, [])
        self.assertEqual(harness.player.resume_requests, [])
        self.assertEqual(
            harness.presenter.calls,
            ["emphasize-timeline"] * 4,
        )
        self.assertEqual(
            harness.router.input_quarantine.last_seen,
            {"right": 0.616},
        )
        self.assertEqual(harness.router.input_quarantine.deadlines, {})
        visible_targets = [
            snapshot["target"]
            for snapshot in harness.publisher.snapshots
            if snapshot["active"]
        ]
        self.assertTrue(visible_targets)
        self.assertGreaterEqual(min(visible_targets), 110.0)

    def test_hold_inference_survives_delayed_osd_focus(self):
        for timeline_from in (1, 2, 3):
            with self.subTest(timeline_from=timeline_from):
                harness = PlaybackOsdHarness()
                observations = harness.promote_right_hold(timeline_from)

                self.assertEqual(
                    [observation[0] for observation in observations],
                    [110.0] * 4,
                )
                self.assertEqual(
                    [observation[3] for observation in observations],
                    [0, 1, 2, 0],
                )
                self.assertEqual(
                    [observation[4] for observation in observations],
                    [1] * 4,
                )
                self.assertEqual(harness.controller.state, PAUSE_PENDING)
                self.assertEqual(harness.controller.source, "hold")
                self.assertEqual(len(harness.player.pause_requests), 1)
                self.assertEqual(harness.player.seek_requests, [])
                self.assertEqual(
                    harness.presenter.calls,
                    ["emphasize-timeline"] * 4,
                )

    def test_confirmed_hold_seeks_once_then_resumes_owned_pause(self):
        harness = PlaybackOsdHarness()
        harness.promote_right_hold()

        harness.ack_pause(0.650)
        self.assertEqual(harness.controller.state, SCRUB_ACTIVE)
        self.assertTrue(harness.controller.controller_paused)
        self.assertEqual(harness.controller.target, 110.0)

        harness.controller.tick(0.900)
        self.assertTrue(harness.controller.hold_released)
        released_target = harness.controller.target
        self.assertGreaterEqual(released_target, 110.0)
        harness.controller.tick(1.400)
        self.assertEqual(harness.controller.target, released_target)

        harness.route("osd-primary", 1.500)
        self.assertEqual(harness.controller.state, COMMITTING)
        self.assertEqual(len(harness.player.seek_requests), 1)
        requested_target, operation, identity, epoch = (
            harness.player.seek_requests[0]
        )
        self.assertAlmostEqual(requested_target, released_target)
        self.assertTrue(operation.startswith("seek:"))
        self.assertEqual((identity, epoch), ("item-one", 1))
        self.assertEqual(len(harness.player.pause_requests), 1)
        self.assertEqual(harness.player.resume_requests, [])

        harness.ack_seek(1.550)
        self.assertEqual(harness.controller.state, RESUME_PENDING)
        self.assertEqual(len(harness.player.seek_requests), 1)
        self.assertEqual(len(harness.player.resume_requests), 1)
        self.assertEqual(
            harness.player.resume_requests[0][1:],
            ("item-one", 1),
        )

        harness.ack_resume(1.600)
        self.assertEqual(harness.controller.state, IDLE)
        harness.now[0] = 1.600
        harness.router.tick()
        self.assertEqual(len(harness.player.pause_requests), 1)
        self.assertEqual(len(harness.player.seek_requests), 1)
        self.assertEqual(len(harness.player.resume_requests), 1)
        self.assertFalse(harness.player.paused)
        self.assertEqual(harness.builtins, [])
        self.assertEqual(
            harness.presenter.calls,
            ["emphasize-timeline"] * 4 + ["focus-transport"],
        )

    def test_incomplete_hold_probe_becomes_two_exact_slow_skips(self):
        harness = PlaybackOsdHarness()

        harness.route("right", 0.0)
        harness.route("timeline-right", 0.400)
        self.assertEqual(harness.controller.target, 110.0)
        self.assertEqual(harness.controller.probe_count, 1)

        harness.controller.tick(0.581)
        self.assertEqual(harness.controller.target, 120.0)
        self.assertEqual(harness.controller.probe_count, 0)
        self.assertEqual(harness.player.pause_requests, [])
        self.assertEqual(harness.player.seek_requests, [])

        harness.controller.tick(0.951)
        self.assertEqual(harness.controller.state, SKIP_SETTLING)
        self.assertEqual(len(harness.player.seek_requests), 1)
        self.assertEqual(harness.player.seek_requests[0][0], 120.0)
        self.assertEqual(harness.player.pause_requests, [])
        self.assertEqual(harness.player.resume_requests, [])

        harness.ack_seek(1.000)
        self.assertEqual(harness.controller.state, IDLE)
        self.assertEqual(len(harness.player.seek_requests), 1)
        self.assertEqual(harness.player.pause_requests, [])
        self.assertEqual(harness.player.resume_requests, [])

    def test_isolated_hidden_right_auto_commits_exactly_ten_seconds(self):
        harness = PlaybackOsdHarness()

        harness.route("right", 0.0)
        self.assertEqual(harness.controller.state, SKIP_ACTIVE)
        self.assertEqual(harness.controller.target, 110.0)
        self.assertEqual(harness.player.pause_requests, [])
        self.assertEqual(harness.player.seek_requests, [])

        harness.controller.tick(0.549)
        self.assertEqual(harness.controller.state, SKIP_ACTIVE)
        self.assertEqual(harness.player.seek_requests, [])

        harness.controller.tick(0.550)
        self.assertEqual(harness.controller.state, SKIP_SETTLING)
        self.assertEqual(len(harness.player.seek_requests), 1)
        target, operation, identity, epoch = harness.player.seek_requests[0]
        self.assertEqual(target, 110.0)
        self.assertTrue(operation.startswith("skip:"))
        self.assertEqual((identity, epoch), ("item-one", 1))
        self.assertEqual(harness.player.pause_requests, [])
        self.assertEqual(harness.player.resume_requests, [])

        harness.ack_seek(0.700)
        self.assertEqual(harness.controller.state, IDLE)
        self.assertEqual(len(harness.player.seek_requests), 1)
        self.assertEqual(harness.player.pause_requests, [])
        self.assertEqual(harness.player.resume_requests, [])


if __name__ == "__main__":
    unittest.main()
