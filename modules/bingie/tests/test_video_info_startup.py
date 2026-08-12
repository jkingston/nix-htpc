from pathlib import Path
import os
import re
import unittest
import xml.etree.ElementTree as ET


SKIN_ROOT = Path(
    os.environ.get(
        "BINGIE_SKIN_ROOT",
        str(Path(__file__).resolve().parents[1] / "src"),
    )
)


class VideoInfoStartupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ET.parse(SKIN_ROOT / "1080i" / "DialogVideoInfo.xml").getroot()

    def test_episode_metadata_helpers_share_one_process(self):
        commands = [node.text or "" for node in self.root.findall("onload")]
        combined = [
            command
            for command in commands
            if "action=gettvshowid,action=ismylist" in command
        ]
        self.assertEqual(len(combined), 1)
        self.assertIn("dbid=$INFO[ListItem.DBID]", combined[0])

    def test_standalone_mylist_probe_excludes_episodes(self):
        probes = [
            node
            for node in self.root.findall("onload")
            if (node.text or "").endswith("action=ismylist)")
        ]
        self.assertEqual(len(probes), 1)
        self.assertNotIn("episode", probes[0].get("condition", "").lower())

    def test_every_primary_play_control_has_an_affirmative_handoff(self):
        source = (
            SKIN_ROOT / "1080i" / "IncludesDialogVideoInfo.xml"
        ).read_text(encoding="utf-8")
        self.assertEqual(source.count("SetProperty(BingiePlaybackStarting,1,Home)"), 10)
        self.assertEqual(source.count("AlarmClock(BingiePlaybackTimeout,"), 10)
        for control_id in ("51", "52", "80", "90", "8181"):
            self.assertEqual(
                len(re.findall(rf'<control type="radiobutton" id="{control_id}">', source)),
                2,
            )

        # Playback deliberately closes the modal before PlayMedia so stopping
        # playback cannot reveal a stale information dialog underneath it.
        self.assertNotRegex(
            source,
            r"AlarmClock\(PlayMovie,PlayMedia\([^<]+</onclick>\s*"
            r"<onclick[^>]*>Dialog.Close\(movieinformation\)",
        )

    def test_home_owns_a_visual_playback_transition_surface(self):
        home_root = ET.parse(SKIN_ROOT / "1080i" / "Home.xml").getroot()
        serialized = ET.tostring(home_root, encoding="unicode")
        self.assertIn("Window(Home).Property(BingiePlaybackStarting)", serialized)
        self.assertNotIn("BingiePlaybackTitle", serialized)

    def test_fullscreen_entry_clears_the_transition_state(self):
        fullscreen_root = ET.parse(
            SKIN_ROOT / "1080i" / "VideoFullScreen.xml"
        ).getroot()
        actions = [node.text or "" for node in fullscreen_root.findall("onload")]
        self.assertIn("ClearProperty(BingiePlaybackStarting,Home)", actions)


if __name__ == "__main__":
    unittest.main()
