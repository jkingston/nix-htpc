import os
from pathlib import Path
import tempfile
import unittest

from reconcile import ReconcileError, reconcile


class ReconcileTests(unittest.TestCase):
    def test_creates_and_then_idempotently_preserves_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "profile/guisettings.xml"
            self.assertTrue(reconcile(path, "skin.bingie", os.getuid(), os.getgid()))
            first = path.read_bytes()
            self.assertFalse(reconcile(path, "skin.bingie", os.getuid(), os.getgid()))
            self.assertEqual(path.read_bytes(), first)
            self.assertIn(b">skin.bingie<", first)

    def test_preserves_unrelated_settings_when_changing_skin(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "guisettings.xml"
            path.write_text(
                '<settings version="2"><setting id="other">keep</setting>'
                '<setting id="lookandfeel.skin">skin.estuary</setting></settings>',
                encoding="utf-8",
            )
            reconcile(path, "skin.bingie", os.getuid(), os.getgid())
            value = path.read_text(encoding="utf-8")
            self.assertIn('<setting id="other">keep</setting>', value)
            self.assertIn('>skin.bingie<', value)

    def test_rejects_duplicate_skin_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "guisettings.xml"
            path.write_text(
                '<settings><setting id="lookandfeel.skin">a</setting>'
                '<setting id="lookandfeel.skin">b</setting></settings>',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ReconcileError, "duplicate"):
                reconcile(path, "skin.bingie", os.getuid(), os.getgid())

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("<settings/>", encoding="utf-8")
            path = root / "guisettings.xml"
            path.symlink_to(target)
            with self.assertRaisesRegex(ReconcileError, "metadata"):
                reconcile(path, "skin.bingie", os.getuid(), os.getgid())


if __name__ == "__main__":
    unittest.main()
