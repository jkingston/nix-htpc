from __future__ import annotations

import os
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
HTPC_HOME_MODULE = Path(
    os.environ.get(
        "HTPC_HOME_MODULE",
        str(REPOSITORY_ROOT / "modules" / "htpc-home.nix"),
    )
).resolve()


def _module_source() -> str:
    return HTPC_HOME_MODULE.read_text(encoding="utf-8")


def _managed_keymap(source: str) -> ET.Element:
    matches = tuple(re.finditer(
        r'home\.file\."\.kodi/userdata/keymaps/'
        r'zz-htpc-remote\.xml"\.text\s*=\s*\'\''
        r"(?P<xml>.*?)"
        r"^\s*'';",
        source,
        flags=re.MULTILINE | re.DOTALL,
    ))
    if len(matches) != 1:
        raise AssertionError(
            "expected exactly one managed Kodi remote keymap assignment"
        )
    root = ET.fromstring(matches[0].group("xml").strip())
    if root.tag != "keymap":
        raise AssertionError("managed remote document root must be <keymap>")
    return root


def _bindings(root: ET.Element, window: str, device: str) -> dict[str, str]:
    windows = root.findall(f"./{window}")
    if len(windows) != 1:
        raise AssertionError(f"expected exactly one {window} keymap")
    devices = windows[0].findall(f"./{device}")
    if len(devices) != 1:
        raise AssertionError(
            f"expected exactly one {window}/{device} keymap"
        )
    node = devices[0]
    tags = [child.tag for child in node]
    if len(tags) != len(set(tags)):
        raise AssertionError(
            f"duplicate bindings in {window}/{device} keymap"
        )
    return {
        child.tag: (child.text or "").strip()
        for child in node
    }


class ManagedRemoteKeymapContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _module_source()
        cls.root = _managed_keymap(cls.source)

    def test_document_only_defines_the_owned_playback_windows(self):
        self.assertEqual(
            [child.tag for child in self.root],
            ["FullscreenVideo", "VideoOSD"],
        )
        for window in self.root:
            self.assertEqual(
                [child.tag for child in window],
                ["remote", "keyboard"],
            )

    def test_fullscreen_remote_opens_or_routes_to_owned_interactions(self):
        self.assertEqual(
            _bindings(self.root, "FullscreenVideo", "remote"),
            {
                "up": "ActivateWindow(VideoOSD)",
                "down": "ActivateWindow(VideoOSD)",
                "left": "NotifyAll(htpc.seek,left)",
                "right": "NotifyAll(htpc.seek,right)",
                "select": "NotifyAll(htpc.seek,primary)",
                "back": "NotifyAll(htpc.seek,fullscreen-back)",
            },
        )

    def test_fullscreen_keyboard_matches_the_remote_contract(self):
        self.assertEqual(
            _bindings(self.root, "FullscreenVideo", "keyboard"),
            {
                "up": "ActivateWindow(VideoOSD)",
                "down": "ActivateWindow(VideoOSD)",
                "left": "NotifyAll(htpc.seek,left)",
                "right": "NotifyAll(htpc.seek,right)",
                "enter": "NotifyAll(htpc.seek,primary)",
                "backspace": "NotifyAll(htpc.seek,fullscreen-back)",
                "escape": "NotifyAll(htpc.seek,fullscreen-back)",
            },
        )

    def test_osd_directions_remain_owned_by_the_focused_skin_control(self):
        self.assertEqual(
            _bindings(self.root, "VideoOSD", "remote"),
            {
                "select": "NotifyAll(htpc.seek,osd-primary)",
                "back": "NotifyAll(htpc.seek,osd-back)",
            },
        )
        self.assertEqual(
            _bindings(self.root, "VideoOSD", "keyboard"),
            {
                "enter": "NotifyAll(htpc.seek,osd-primary)",
                "backspace": "NotifyAll(htpc.seek,osd-back)",
                "escape": "NotifyAll(htpc.seek,osd-back)",
            },
        )

    def test_known_legacy_fullscreen_keymap_is_removed(self):
        self.assertEqual(
            self.source.count(
                "/home/htpc/.kodi/userdata/keymaps/"
                "cec-stop-playback.xml"
            ),
            1,
        )
        self.assertRegex(
            self.source,
            r"rm -f\s*\\\s*\n\s*"
            r"/home/htpc/\.kodi/userdata/keymaps/"
            r"cec-stop-playback\.xml",
        )


if __name__ == "__main__":
    unittest.main()
