import os
from pathlib import Path
import unittest


SKIN_ROOT = Path(os.environ.get(
    "BINGIE_SKIN_ROOT",
    Path(__file__).resolve().parents[1] / "src",
))
EPISODES_XML = SKIN_ROOT / "1080i" / "View_525_Bingie_Episodes.xml"
SEASONS_XML = SKIN_ROOT / "1080i" / "View_527_Bingie_Seasons.xml"
VIDEO_NAV_XML = SKIN_ROOT / "1080i" / "MyVideoNav.xml"


class EpisodePickerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.episodes_source = EPISODES_XML.read_text(encoding="utf-8")
        cls.seasons_source = SEASONS_XML.read_text(encoding="utf-8")
        cls.video_nav_source = VIDEO_NAV_XML.read_text(encoding="utf-8")

    def test_episode_and_season_sources_are_explicitly_separated(self):
        self.assertIn("videodb://tvshows/titles/,/-2/", self.episodes_source)
        self.assertIn("videodb://tvshows/titles/,/]</value>", self.episodes_source)
        self.assertIn(
            '<content target="videos" sortby="episode" sortorder="ascending">$VAR[View525EpisodesContent]</content>',
            self.episodes_source,
        )

    def test_season_click_requests_an_anchor_instead_of_replacing_window(self):
        self.assertIn(
            "SetProperty(BingieEpisodeAnchorSeason,$INFO[Container(5250).ListItem.Season],Home)",
            self.episodes_source,
        )
        self.assertNotIn("ReplaceWindow(Videos,$ESCINFO[Container(5250).ListItem.FolderPath]", self.episodes_source)
        self.assertIn("<onclick>noop</onclick>", self.episodes_source)

    def test_anchor_indexing_starts_when_the_episode_window_opens(self):
        self.assertIn(
            "SetProperty(BingieEpisodeAnchorShowID,$INFO[Window(Home).Property(ListItem.TVShowID)],Home)",
            self.video_nav_source,
        )

    def test_standalone_season_view_uses_all_episodes_without_navigation(self):
        self.assertIn("videodb://tvshows/titles/,/-2/", self.seasons_source)
        self.assertIn("Container(527).ListItem.TVShowDBID", self.seasons_source)
        self.assertIn(
            '<content target="videos" sortby="episode" sortorder="ascending">$VAR[View527EpisodesContent]</content>',
            self.seasons_source,
        )
        self.assertIn(
            "SetProperty(BingieEpisodeAnchorSeason,$INFO[Container(527).ListItem.Season],Home)",
            self.seasons_source,
        )
        self.assertNotIn(
            "$INFO[Container(527).ListItem.FolderPath]</content>",
            self.seasons_source,
        )


if __name__ == "__main__":
    unittest.main()
