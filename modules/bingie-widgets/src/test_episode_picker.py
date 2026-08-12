import unittest

from resources.lib.episode_picker import next_picker_position, season_anchor_positions


class EpisodePickerTests(unittest.TestCase):
    def test_advances_an_absolute_position(self):
        self.assertEqual(next_picker_position("4"), "5")

    def test_invalid_position_is_ignored(self):
        self.assertEqual(next_picker_position(""), "")
        self.assertEqual(next_picker_position("not-a-number"), "")

    def test_season_anchors_follow_all_episode_order(self):
        episodes = [
            {"episodeid": 12, "season": 2, "episode": 2},
            {"episodeid": 10, "season": 1, "episode": 2},
            {"episodeid": 9, "season": 1, "episode": 1},
            {"episodeid": 13, "season": 2, "episode": 1},
            {"episodeid": 8, "season": 0, "episode": 1},
        ]

        self.assertEqual(
            season_anchor_positions(episodes),
            {"0": "1", "1": "2", "2": "4"},
        )

    def test_invalid_episode_numbers_do_not_create_anchors(self):
        self.assertEqual(
            season_anchor_positions([{"season": "", "episode": 1}]),
            {},
        )


if __name__ == "__main__":
    unittest.main()
