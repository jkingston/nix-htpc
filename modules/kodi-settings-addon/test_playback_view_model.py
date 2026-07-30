from __future__ import absolute_import, division, print_function

import unittest

from playback_view_model import PlaybackViewModel
from seek_controller import (
    CANCEL_WAIT_PAUSE,
    COMMITTING,
    IDLE,
    PAUSE_PENDING,
    RESUME_PENDING,
    SCRUB_ACTIVE,
    SKIP_ACTIVE,
    SKIP_SETTLING,
)


def player(current=100.0, duration=1000.0, paused=False, identity="movie", epoch=1):
    return {
        "seekable": True,
        "current": current,
        "duration": duration,
        "paused": paused,
        "identity": identity,
        "epoch": epoch,
    }


def controller(
    state=SKIP_ACTIVE,
    target=110.0,
    generation=1,
    epoch=1,
    source="timeline",
    **extra
):
    try:
        target_seconds = int(round(target))
    except (TypeError, ValueError, OverflowError):
        target_seconds = target
    snapshot = {
        "active": state != IDLE,
        "state": state,
        "target": target,
        "target_seconds": target_seconds,
        "generation": generation,
        "playback_epoch": epoch,
        "source": source,
        "hold": False,
        "hold_released": False,
        "pending_operation": None,
        "skip_operation": None,
        "skip_requested_target": None,
        "resume_reason": None,
        "handoff_active": False,
        "handoff_target": None,
        "handoff_identity": None,
        "handoff_epoch": None,
    }
    snapshot.update(extra)
    return snapshot


class PlaybackViewModelTest(unittest.TestCase):
    def test_skip_callback_latches_actual_until_raw_progress_settles(self):
        view = PlaybackViewModel()
        first = view.update(controller(), player(), 0.0)
        self.assertEqual(first["phase"], "skip")
        self.assertEqual(first["actual_seconds"], 100)
        self.assertEqual(first["target_seconds"], 110)

        applying = controller(
            state=SKIP_SETTLING,
            skip_operation="skip-a",
            skip_requested_target=110,
        )
        view.update(applying, player(current=99), 0.55)
        view.on_player_event(
            "seeked",
            {
                "operation": "skip-a",
                "time": 110,
                "identity": "movie",
                "epoch": 1,
            },
            0.60,
        )

        settling = view.update(
            controller(state=IDLE, target=0),
            player(current=100),
            0.61,
        )
        self.assertTrue(settling["active"])
        self.assertEqual(settling["phase"], "settling")
        self.assertEqual(settling["actual_seconds"], 110)
        self.assertEqual(settling["target_seconds"], 110)

        view.update(controller(state=IDLE, target=0), player(current=110), 0.66)
        settled = view.update(
            controller(state=IDLE, target=0),
            player(current=110.2),
            0.71,
        )
        self.assertFalse(settled["active"])
        self.assertEqual(settled["phase"], "idle")

    def test_oscillating_raw_progress_never_regresses_before_stable_handoff(self):
        view = PlaybackViewModel()
        applying = controller(
            state=SKIP_SETTLING,
            target=110,
            skip_operation="skip-a",
            skip_requested_target=110,
        )
        view.update(applying, player(), 0.0)
        view.on_player_event(
            "seeked",
            {
                "operation": "skip-a",
                "time": 110,
                "identity": "movie",
                "epoch": 1,
            },
            0.1,
        )

        for timestamp, raw_position in (
            (0.2, 110),
            (0.3, 100),
            (1.7, 110),
            (1.8, 100),
            (2.5, 110),
        ):
            snapshot = view.update(
                controller(state=IDLE, target=0),
                player(current=raw_position),
                timestamp,
            )
            self.assertTrue(snapshot["active"])
            self.assertEqual(snapshot["target_seconds"], 110)
            self.assertEqual(snapshot["targetmarker"], "11.0000,11.0000")

        settled = view.update(
            controller(state=IDLE, target=0),
            player(current=110),
            2.6,
        )
        self.assertFalse(settled["active"])

    def test_matching_preview_survives_callback_to_raw_position_handoff(self):
        view = PlaybackViewModel()
        applying = controller(
            state=SKIP_SETTLING,
            target=110,
            skip_operation="skip-a",
            skip_requested_target=110,
        )
        view.update(applying, player(), 0.0)
        self.assertTrue(view.offer_preview("/tmp/frame-110.jpg", 1, 110))
        view.on_player_event(
            "seeked",
            {
                "operation": "skip-a",
                "time": 110,
                "identity": "movie",
                "epoch": 1,
            },
            0.1,
        )

        handoff = view.update(
            controller(state=IDLE, target=0),
            player(current=100),
            0.11,
        )
        self.assertEqual(handoff["phase"], "settling")
        self.assertEqual(handoff["targetmarker"], "11.0000,11.0000")
        self.assertTrue(view.offer_preview("", 1, 0))
        retained = view.snapshot()
        self.assertEqual(retained["preview_status"], "ready")
        self.assertEqual(retained["preview_path"], "/tmp/frame-110.jpg")

        view.update(controller(state=IDLE, target=0), player(current=110), 0.2)
        settled = view.update(
            controller(state=IDLE, target=0),
            player(current=110),
            0.3,
        )
        self.assertFalse(settled["active"])
        self.assertEqual(settled["preview_status"], "none")

    def test_controller_handoff_covers_a_missing_skip_callback(self):
        view = PlaybackViewModel()
        view.update(
            controller(
                state=SKIP_SETTLING,
                target=110,
                skip_operation="lost-callback",
                skip_requested_target=110,
            ),
            player(current=100),
            0.0,
        )

        settling = view.update(
            controller(
                state=IDLE,
                target=0,
                handoff_active=True,
                handoff_target=110,
                handoff_identity="movie",
                handoff_epoch=1,
            ),
            player(current=100),
            4.01,
        )
        self.assertTrue(settling["active"])
        self.assertEqual(settling["phase"], "settling")
        self.assertEqual(settling["actual_seconds"], 110)
        self.assertEqual(settling["target_seconds"], 110)

        for timestamp, raw_position in ((4.1, 110), (4.2, 100), (4.3, 110)):
            snapshot = view.update(
                controller(
                    state=IDLE,
                    target=0,
                    handoff_active=True,
                    handoff_target=110,
                    handoff_identity="movie",
                    handoff_epoch=1,
                ),
                player(current=raw_position),
                timestamp,
            )
            self.assertTrue(snapshot["active"])
            self.assertEqual(snapshot["target_seconds"], 110)

        settled = view.update(
            controller(state=IDLE, target=0),
            player(current=110),
            4.4,
        )
        self.assertFalse(settled["active"])

    def test_handoff_is_identity_and_epoch_bound(self):
        view = PlaybackViewModel()
        initial = view.update(
            controller(
                state=IDLE,
                target=0,
                handoff_active=True,
                handoff_target=900,
                handoff_identity="other",
                handoff_epoch=2,
            ),
            player(current=100),
            0.0,
        )
        self.assertFalse(initial["active"])
        self.assertEqual(initial["actual_seconds"], 100)

    def test_resume_pending_adopts_handoff_before_controller_clears_it(self):
        view = PlaybackViewModel()
        view.update(
            controller(
                state=COMMITTING,
                target=150,
                pending_operation="lost-seek",
            ),
            player(current=100, paused=True),
            0.0,
        )
        during_resume = view.update(
            controller(
                state=RESUME_PENDING,
                target=150,
                resume_reason="commit",
                handoff_active=True,
                handoff_target=150,
                handoff_identity="movie",
                handoff_epoch=1,
            ),
            player(current=150, paused=True),
            4.01,
        )
        self.assertTrue(during_resume["active"])
        self.assertEqual(during_resume["target_seconds"], 150)

        handoff_cleared = view.update(
            controller(
                state=RESUME_PENDING,
                target=150,
                resume_reason="commit",
            ),
            player(current=100, paused=True),
            4.10,
        )
        self.assertTrue(handoff_cleared["active"])
        self.assertEqual(handoff_cleared["target_seconds"], 150)

        after_resume = view.update(
            controller(state=IDLE, target=0),
            player(current=100, paused=False),
            4.20,
        )
        self.assertTrue(after_resume["active"])
        self.assertEqual(after_resume["phase"], "settling")
        self.assertEqual(after_resume["target_seconds"], 150)

    def test_out_of_order_older_callback_cannot_regress_actual(self):
        view = PlaybackViewModel()
        view.update(
            controller(
                state=SKIP_SETTLING,
                target=110,
                skip_operation="skip-a",
                skip_requested_target=110,
            ),
            player(),
            0.0,
        )
        view.update(
            controller(
                state=SKIP_ACTIVE,
                target=120,
                skip_operation="skip-a",
                skip_requested_target=110,
            ),
            player(),
            0.1,
        )
        view.update(
            controller(
                state=SKIP_SETTLING,
                target=120,
                skip_operation="skip-b",
                skip_requested_target=120,
            ),
            player(),
            0.2,
        )
        view.on_player_event(
            "seeked",
            {
                "operation": "skip-b",
                "time": 120,
                "identity": "movie",
                "epoch": 1,
            },
            0.3,
        )
        view.on_player_event(
            "seeked",
            {
                "operation": "skip-a",
                "time": 110,
                "identity": "movie",
                "epoch": 1,
            },
            0.4,
        )
        self.assertEqual(view.snapshot()["actual_seconds"], 120)

    def test_retired_operation_callback_cannot_move_newer_target(self):
        view = PlaybackViewModel()
        view.update(
            controller(
                state=SKIP_SETTLING,
                target=110,
                skip_operation="timed-out",
                skip_requested_target=110,
            ),
            player(),
            0.0,
        )
        newer = view.update(
            controller(
                state=SKIP_ACTIVE,
                target=130,
                generation=2,
                skip_operation=None,
                skip_requested_target=None,
            ),
            player(),
            0.1,
        )
        self.assertEqual(newer["target_seconds"], 130)
        view.on_player_event(
            "seeked",
            {
                "operation": "timed-out",
                "time": 110,
                "identity": "movie",
                "epoch": 1,
            },
            0.2,
        )
        snapshot = view.snapshot()
        self.assertEqual(snapshot["actual_seconds"], 100)
        self.assertEqual(snapshot["target_seconds"], 130)

    def test_hold_prompt_appears_only_after_release(self):
        view = PlaybackViewModel()
        pausing = view.update(
            controller(
                state=PAUSE_PENDING,
                hold=True,
                source="hold",
            ),
            player(current=100.5),
            0.0,
        )
        self.assertEqual(pausing["phase"], "pausing")
        self.assertEqual(pausing["prompt"], "")

        moving = view.update(
            controller(
                state=SCRUB_ACTIVE,
                target=140,
                hold=True,
                hold_released=False,
                source="hold",
            ),
            player(current=100.5, paused=True),
            0.1,
        )
        self.assertEqual(moving["phase"], "scrubbing")
        self.assertEqual(moving["prompt"], "")

        ready = view.update(
            controller(
                state=SCRUB_ACTIVE,
                target=140,
                hold=True,
                hold_released=True,
                source="hold",
            ),
            player(current=100.5, paused=True),
            0.4,
        )
        self.assertEqual(ready["phase"], "ready")
        self.assertIn("Back", ready["prompt"])

    def test_cancellation_invalidates_target_and_preview_once(self):
        view = PlaybackViewModel()
        view.update(
            controller(
                state=SCRUB_ACTIVE,
                target=140,
                hold=True,
                hold_released=True,
                source="hold",
            ),
            player(paused=True),
            0.0,
        )
        self.assertTrue(view.offer_preview("/tmp/frame.jpg", 1, 140))

        cancelled = view.update(
            controller(
                state=CANCEL_WAIT_PAUSE,
                target=140,
                hold=True,
                source="hold",
            ),
            player(paused=True),
            0.1,
        )
        self.assertFalse(cancelled["active"])
        self.assertFalse(cancelled["target_valid"])
        self.assertEqual(cancelled["preview_path"], "")

        resuming = view.update(
            controller(
                state=RESUME_PENDING,
                target=140,
                source="hold",
                resume_reason="cancel",
            ),
            player(paused=True),
            0.2,
        )
        self.assertFalse(resuming["active"])
        self.assertFalse(resuming["target_valid"])

    def test_preview_must_match_current_generation_and_target(self):
        view = PlaybackViewModel()
        view.update(controller(target=110, generation=4), player(), 0.0)
        self.assertFalse(view.offer_preview("/tmp/old.jpg", 3, 110))
        self.assertEqual(view.snapshot()["preview_status"], "loading")
        self.assertTrue(view.offer_preview("/tmp/110.jpg", 4, 110))
        self.assertEqual(view.snapshot()["preview_status"], "ready")

        changed = view.update(
            controller(target=120, generation=4),
            player(),
            0.1,
        )
        self.assertEqual(changed["preview_status"], "loading")
        self.assertEqual(changed["preview_path"], "")
        self.assertFalse(view.offer_preview("/tmp/110.jpg", 4, 110))
        self.assertTrue(view.offer_preview("/tmp/120.jpg", 4, 120))

    def test_media_epoch_and_stale_callback_cannot_leak(self):
        view = PlaybackViewModel()
        view.update(
            controller(
                state=SKIP_SETTLING,
                skip_operation="old",
                skip_requested_target=110,
            ),
            player(),
            0.0,
        )
        changed = view.update(
            controller(state=IDLE, target=0, epoch=2),
            player(current=5, identity="other", epoch=2),
            0.1,
        )
        self.assertFalse(changed["active"])
        self.assertEqual(changed["actual_seconds"], 5)
        view.on_player_event(
            "seeked",
            {
                "operation": "old",
                "time": 110,
                "identity": "movie",
                "epoch": 1,
            },
            0.2,
        )
        self.assertEqual(view.snapshot()["actual_seconds"], 5)

    def test_nan_inf_and_boundaries_produce_safe_geometry(self):
        view = PlaybackViewModel()
        invalid = view.update(
            controller(target=float("nan")),
            player(),
            0.0,
        )
        self.assertFalse(invalid["active"])
        self.assertFalse(invalid["target_valid"])
        self.assertEqual(invalid["targetfill"], "0.0000,0.0000")
        self.assertEqual(invalid["targetmarker"], "0.0000,0.0000")

        infinite = view.update(controller(target=float("inf")), player(), 0.05)
        self.assertFalse(infinite["active"])
        self.assertFalse(infinite["target_valid"])

        low = view.update(controller(target=-50), player(), 0.1)
        self.assertEqual(low["targetfill"], "0.0000,0.0000")
        self.assertEqual(low["targetmarker"], "0.0000,0.0000")

        high = view.update(controller(target=5000), player(), 0.2)
        self.assertEqual(high["targetfill"], "0.0000,100.0000")
        self.assertEqual(high["targetmarker"], "100.0000,100.0000")

    def test_unattributed_callback_and_settle_timeout_are_safe(self):
        view = PlaybackViewModel()
        view.update(controller(), player(), 0.0)
        view.on_player_event(
            "seeked",
            {
                "operation": None,
                "time": 900,
                "identity": "movie",
                "epoch": 1,
            },
            0.1,
        )
        self.assertEqual(view.snapshot()["actual_seconds"], 100)

        applying = controller(
            state=COMMITTING,
            target=150,
            pending_operation="seek-one",
        )
        view.update(applying, player(paused=True), 0.2)
        view.on_player_event(
            "seeked",
            {
                "operation": "seek-one",
                "time": 150,
                "identity": "movie",
                "epoch": 1,
            },
            0.3,
        )
        still_settling = view.update(
            controller(state=IDLE, target=0),
            player(current=100),
            0.4,
        )
        self.assertTrue(still_settling["active"])
        timed_out = view.update(
            controller(state=IDLE, target=0),
            player(current=100),
            4.31,
        )
        self.assertFalse(timed_out["active"])


if __name__ == "__main__":
    unittest.main()
