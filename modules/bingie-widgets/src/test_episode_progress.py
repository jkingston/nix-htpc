import unittest

from resources.lib.episode_progress import (
    CONTINUE,
    UP_NEXT,
    resolve_series_primary,
    resolve_series_primaries,
    resolve_series_progress,
)


def episode(
        episode_id, number, show_id=1, season=1, played=0, lastplayed="",
        resume=0):
    return {
        "episodeid": episode_id,
        "tvshowid": show_id,
        "season": season,
        "episode": number,
        "playcount": played,
        "lastplayed": lastplayed,
        "resume": {"position": resume, "total": 1800},
    }


class EpisodeProgressTests(unittest.TestCase):
    def test_unfinished_latest_episode_is_continue_watching_only(self):
        decisions = resolve_series_progress([
            episode(1, 1, played=1, lastplayed="2026-01-01 10:00:00"),
            episode(2, 2, lastplayed="2026-01-02 10:00:00", resume=300),
            episode(3, 3),
        ])

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].state, CONTINUE)
        self.assertEqual(decisions[0].target_episodeid, 2)

    def test_completed_episode_advances_to_immediate_successor(self):
        decisions = resolve_series_progress([
            episode(1, 1),
            episode(2, 2, played=1, lastplayed="2026-01-02 10:00:00"),
            episode(3, 3),
        ])

        self.assertEqual(decisions[0].state, UP_NEXT)
        self.assertEqual(decisions[0].target_episodeid, 3)

    def test_skipped_unwatched_episode_does_not_pull_sequence_backwards(self):
        decisions = resolve_series_progress([
            episode(1, 1, played=1, lastplayed="2026-01-01 10:00:00"),
            episode(2, 2),
            episode(3, 3, played=1, lastplayed="2026-01-03 10:00:00"),
            episode(4, 4),
        ])

        self.assertEqual(decisions[0].target_episodeid, 4)

    def test_rewatch_can_recommend_an_already_watched_successor(self):
        decisions = resolve_series_progress([
            episode(1, 1, played=2, lastplayed="2026-01-03 10:00:00"),
            episode(2, 2, played=1, lastplayed="2025-01-02 10:00:00"),
        ])

        self.assertEqual(decisions[0].target_episodeid, 2)

    def test_newer_completed_playback_supersedes_stale_resume(self):
        decisions = resolve_series_progress([
            episode(1, 1, lastplayed="2026-01-01 10:00:00", resume=300),
            episode(2, 2, played=1, lastplayed="2026-01-02 10:00:00"),
            episode(3, 3),
        ])

        self.assertEqual(decisions[0].state, UP_NEXT)
        self.assertEqual(decisions[0].target_episodeid, 3)

    def test_latest_of_multiple_resumes_wins(self):
        decisions = resolve_series_progress([
            episode(1, 1, lastplayed="2026-01-02 10:00:00", resume=200),
            episode(2, 2, lastplayed="2026-01-03 10:00:00", resume=100),
        ])

        self.assertEqual(decisions[0].state, CONTINUE)
        self.assertEqual(decisions[0].target_episodeid, 2)

    def test_brief_open_without_resume_or_playcount_is_ignored(self):
        decisions = resolve_series_progress([
            episode(1, 1, played=1, lastplayed="2026-01-01 10:00:00"),
            episode(2, 2, lastplayed="2026-01-03 10:00:00"),
            episode(3, 3),
        ])

        self.assertEqual(decisions[0].target_episodeid, 2)

    def test_series_finale_has_no_decision(self):
        decisions = resolve_series_progress([
            episode(1, 1, played=1, lastplayed="2026-01-01 10:00:00"),
        ])

        self.assertEqual(decisions, [])

    def test_specials_do_not_disrupt_normal_episode_order(self):
        decisions = resolve_series_progress([
            episode(100, 1, season=0, played=1, lastplayed="2026-01-03 10:00:00"),
            episode(1, 1, played=1, lastplayed="2026-01-01 10:00:00"),
            episode(2, 2),
        ])

        self.assertEqual(decisions[0].target_episodeid, 2)
        self.assertEqual(decisions[0].target_index, 2)

    def test_decisions_are_ordered_by_series_activity(self):
        decisions = resolve_series_progress([
            episode(1, 1, show_id=1, played=1, lastplayed="2026-01-01 10:00:00"),
            episode(2, 2, show_id=1),
            episode(3, 1, show_id=2, resume=100, lastplayed="2026-01-03 10:00:00"),
        ])

        self.assertEqual([decision.tvshowid for decision in decisions], [2, 1])

    def test_show_primary_falls_back_to_first_normal_episode(self):
        decision = resolve_series_primary([
            episode(100, 1, season=0),
            episode(1, 1),
            episode(2, 2),
        ])

        self.assertEqual(decision.target_episodeid, 1)
        self.assertEqual(decision.target_index, 1)

    def test_show_primary_restarts_after_series_finale(self):
        decision = resolve_series_primary([
            episode(1, 1, played=1, lastplayed="2026-01-01 10:00:00"),
        ])

        self.assertEqual(decision.target_episodeid, 1)

    def test_primaries_include_progressed_and_unseen_shows(self):
        decisions = resolve_series_primaries([
            episode(1, 1, show_id=1, played=1,
                    lastplayed="2026-01-01 10:00:00"),
            episode(2, 2, show_id=1),
            episode(3, 1, show_id=2),
        ])

        targets = {
            decision.tvshowid: decision.target_episodeid
            for decision in decisions
        }
        self.assertEqual(targets, {1: 2, 2: 3})


if __name__ == "__main__":
    unittest.main()
