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
            self.assertEqual(snapshot["target_percent"], 11.0)

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
        self.assertEqual(handoff["target_percent"], 11.0)
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
        self.assertIsNone(view.preview_started_at)

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
        self.assertEqual(cancelled["preview_status"], "none")
        self.assertEqual(cancelled["preview_path"], "")
        self.assertIsNone(view.preview_started_at)

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
        self.assertEqual(resuming["preview_status"], "none")
        self.assertEqual(resuming["preview_path"], "")
        self.assertIsNone(view.preview_started_at)

    def test_preview_lifecycle_uses_exact_grace_and_timeout_boundaries(self):
        view = PlaybackViewModel()
        initial = view.update(
            controller(target=110, generation=4),
            player(),
            0.0,
        )
        self.assertEqual(initial["preview_status"], "none")
        self.assertEqual(initial["preview_path"], "")

        before_loading = view.update(
            controller(target=110, generation=4),
            player(),
            0.179,
        )
        self.assertEqual(before_loading["preview_status"], "none")
        at_loading = view.update(
            controller(target=110, generation=4),
            player(),
            0.180,
        )
        self.assertEqual(at_loading["preview_status"], "loading")

        before_unavailable = view.update(
            controller(target=110, generation=4),
            player(),
            1.999,
        )
        self.assertEqual(before_unavailable["preview_status"], "loading")
        at_unavailable = view.update(
            controller(target=110, generation=4),
            player(),
            2.000,
        )
        self.assertEqual(at_unavailable["preview_status"], "unavailable")

    def test_empty_and_stale_preview_offers_do_not_restart_lifecycle(self):
        view = PlaybackViewModel()
        view.update(controller(target=110, generation=4), player(), 0.0)

        self.assertFalse(view.offer_preview("", 4, 110))
        self.assertFalse(view.offer_preview("", 4, 110))
        self.assertFalse(view.offer_preview("/tmp/old-generation.jpg", 3, 110))
        self.assertFalse(view.offer_preview("/tmp/old-target.jpg", 4, 109))
        self.assertEqual(view.snapshot()["preview_status"], "none")
        self.assertEqual(view.snapshot()["preview_path"], "")
        self.assertEqual(view.preview_started_at, 0.0)

        loading = view.update(
            controller(target=110, generation=4),
            player(),
            0.180,
        )
        self.assertEqual(loading["preview_status"], "loading")
        self.assertFalse(view.offer_preview("", 4, 110))
        self.assertFalse(
            view.offer_preview("/tmp/other-generation.jpg", 5, 110)
        )
        self.assertFalse(view.offer_preview("/tmp/other-target.jpg", 4, 120))
        self.assertEqual(view.preview_started_at, 0.0)

        unavailable = view.update(
            controller(target=110, generation=4),
            player(),
            2.000,
        )
        self.assertEqual(unavailable["preview_status"], "unavailable")
        self.assertEqual(unavailable["preview_path"], "")

    def test_exact_preview_can_recover_after_becoming_unavailable(self):
        view = PlaybackViewModel()
        view.update(controller(target=110, generation=4), player(), 0.0)
        unavailable = view.update(
            controller(target=110, generation=4),
            player(),
            2.000,
        )
        self.assertEqual(unavailable["preview_status"], "unavailable")

        self.assertTrue(view.offer_preview("/tmp/110.jpg", 4, 110))
        recovered = view.snapshot()
        self.assertEqual(recovered["preview_status"], "ready")
        self.assertEqual(recovered["preview_path"], "/tmp/110.jpg")

    def test_target_and_generation_changes_restart_preview_grace(self):
        view = PlaybackViewModel()
        view.update(controller(target=110, generation=4), player(), 0.0)
        view.update(controller(target=110, generation=4), player(), 2.0)

        changed_target = view.update(
            controller(target=120, generation=4),
            player(),
            2.1,
        )
        self.assertEqual(changed_target["preview_status"], "none")
        self.assertEqual(changed_target["preview_path"], "")
        self.assertEqual(view.preview_started_at, 2.1)
        still_in_target_grace = view.update(
            controller(target=120, generation=4),
            player(),
            2.279,
        )
        self.assertEqual(still_in_target_grace["preview_status"], "none")
        target_loading = view.update(
            controller(target=120, generation=4),
            player(),
            2.281,
        )
        self.assertEqual(target_loading["preview_status"], "loading")

        changed_generation = view.update(
            controller(target=120, generation=5),
            player(),
            3.0,
        )
        self.assertEqual(changed_generation["preview_status"], "none")
        self.assertEqual(changed_generation["preview_path"], "")
        self.assertEqual(view.preview_started_at, 3.0)
        still_in_generation_grace = view.update(
            controller(target=120, generation=5),
            player(),
            3.179,
        )
        self.assertEqual(still_in_generation_grace["preview_status"], "none")
        generation_loading = view.update(
            controller(target=120, generation=5),
            player(),
            3.181,
        )
        self.assertEqual(generation_loading["preview_status"], "loading")

    def test_sub_microsecond_target_jitter_does_not_restart_preview_grace(self):
        view = PlaybackViewModel()
        view.update(controller(target=110, generation=4), player(), 0.0)

        jittered = view.update(
            controller(target=110.0000004, generation=4),
            player(),
            0.179,
        )
        self.assertEqual(jittered["preview_status"], "none")
        self.assertEqual(view.preview_started_at, 0.0)
        loading = view.update(
            controller(target=110.0000004, generation=4),
            player(),
            0.180,
        )
        self.assertEqual(loading["preview_status"], "loading")
        self.assertEqual(view.preview_started_at, 0.0)

    def test_ready_preview_survives_empty_and_stale_offers(self):
        view = PlaybackViewModel()
        view.update(controller(target=110, generation=4), player(), 0.0)
        self.assertTrue(view.offer_preview("/tmp/110.jpg", 4, 110))

        self.assertFalse(view.offer_preview("", 4, 110))
        self.assertFalse(view.offer_preview("/tmp/old-generation.jpg", 3, 110))
        self.assertFalse(view.offer_preview("/tmp/old-target.jpg", 4, 109))
        retained = view.snapshot()
        self.assertEqual(retained["preview_status"], "ready")
        self.assertEqual(retained["preview_path"], "/tmp/110.jpg")
        still_ready = view.update(
            controller(target=110, generation=4),
            player(),
            10.0,
        )
        self.assertEqual(still_ready["preview_status"], "ready")
        self.assertEqual(still_ready["preview_path"], "/tmp/110.jpg")

    def test_same_target_handoff_preserves_ready_across_generation_change(self):
        view = PlaybackViewModel()
        view.update(controller(target=110, generation=4), player(), 0.0)
        self.assertTrue(view.offer_preview("/tmp/110.jpg", 4, 110))

        handoff = view.update(
            controller(
                state=IDLE,
                target=0,
                generation=5,
                handoff_active=True,
                handoff_target=110,
                handoff_identity="movie",
                handoff_epoch=1,
            ),
            player(current=100),
            0.1,
        )
        self.assertTrue(handoff["active"])
        self.assertEqual(handoff["phase"], "settling")
        self.assertEqual(handoff["target_seconds"], 110)
        self.assertEqual(handoff["preview_status"], "ready")
        self.assertEqual(handoff["preview_path"], "/tmp/110.jpg")

    def test_changed_handoff_target_clears_ready_and_starts_new_grace(self):
        view = PlaybackViewModel()
        view.update(controller(target=110, generation=4), player(), 0.0)
        self.assertTrue(view.offer_preview("/tmp/110.jpg", 4, 110))

        handoff = view.update(
            controller(
                state=IDLE,
                target=0,
                generation=5,
                handoff_active=True,
                handoff_target=120,
                handoff_identity="movie",
                handoff_epoch=1,
            ),
            player(current=100),
            0.1,
        )
        self.assertTrue(handoff["active"])
        self.assertEqual(handoff["phase"], "settling")
        self.assertEqual(handoff["target_seconds"], 120)
        self.assertEqual(handoff["preview_status"], "none")
        self.assertEqual(handoff["preview_path"], "")
        self.assertEqual(view.preview_started_at, 0.1)

    def test_preview_status_does_not_regress_if_clock_moves_backwards(self):
        view = PlaybackViewModel()
        view.update(controller(target=110, generation=4), player(), 10.0)
        loading = view.update(
            controller(target=110, generation=4),
            player(),
            10.181,
        )
        self.assertEqual(loading["preview_status"], "loading")

        before_start = view.update(
            controller(target=110, generation=4),
            player(),
            9.0,
        )
        self.assertEqual(before_start["preview_status"], "loading")
        unavailable = view.update(
            controller(target=110, generation=4),
            player(),
            12.0,
        )
        self.assertEqual(unavailable["preview_status"], "unavailable")
        back_inside_loading_window = view.update(
            controller(target=110, generation=4),
            player(),
            10.5,
        )
        self.assertEqual(
            back_inside_loading_window["preview_status"],
            "unavailable",
        )

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
        self.assertEqual(changed["preview_status"], "none")
        self.assertIsNone(view.preview_started_at)
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
        self.assertEqual(invalid["target_percent"], 0.0)

        infinite = view.update(controller(target=float("inf")), player(), 0.05)
        self.assertFalse(infinite["active"])
        self.assertFalse(infinite["target_valid"])

        low = view.update(controller(target=-50), player(), 0.1)
        self.assertEqual(low["target_percent"], 0.0)

        high = view.update(controller(target=5000), player(), 0.2)
        self.assertEqual(high["target_percent"], 100.0)

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
