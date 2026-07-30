from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "default.py"


class ReviewSourcePolicyTest(unittest.TestCase):
    def test_runtime_has_no_media_device_or_dynamic_execution_surface(self):
        source = RUNTIME.read_text(encoding="utf-8")
        forbidden = (
            "PlayerControl(",
            "PlayMedia(",
            "NotifyAll(",
            "Input.ExecuteAction",
            "CEC",
            "DPMS(",
            "ActivateSource",
            "subprocess",
            "os.system",
            "jsonrpc",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, source)

        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        ]
        self.assertFalse(
            any(
                isinstance(call.func, ast.Name)
                and call.func.id in ("eval", "exec", "compile")
                for call in calls
            )
        )

    def test_builtins_are_closed_constants(self):
        source = RUNTIME.read_text(encoding="utf-8")
        self.assertIn(
            'CLOSE_REVIEW = "Dialog.Close(1192,true)"',
            source,
        )
        self.assertIn('OPEN_REVIEW = "ActivateWindow(1192)"', source)
        self.assertEqual(source.count("xbmc.executebuiltin("), 2)


if __name__ == "__main__":
    unittest.main()
