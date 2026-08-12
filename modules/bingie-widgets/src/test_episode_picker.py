import unittest

from resources.lib.episode_picker import next_picker_position


class EpisodePickerTests(unittest.TestCase):
    def test_advances_an_absolute_position(self):
        self.assertEqual(next_picker_position("4"), "5")

    def test_invalid_position_is_ignored(self):
        self.assertEqual(next_picker_position(""), "")
        self.assertEqual(next_picker_position("not-a-number"), "")


if __name__ == "__main__":
    unittest.main()
