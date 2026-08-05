from pathlib import Path
import os
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


if __name__ == "__main__":
    unittest.main()
