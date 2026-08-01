from __future__ import annotations

import os
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
SKIN_ROOT = Path(os.environ.get("BINGIE_SKIN_ROOT", MODULE_ROOT / "src"))
UPSTREAM_ASSETS = Path(
    os.environ.get(
        "BINGIE_UPSTREAM_ASSETS",
        MODULE_ROOT / "upstream-assets.nix",
    )
)

RETIRED_WINDOWS = (
    "mainWindow.xml",
    "service-LibreELEC-Settings-mainWindow.xml",
    "service-OpenELEC-Settings-mainWindow.xml",
)
RETIRED_REFERENCES = (
    "extras/openelec",
    "libreelec.settings",
    "mainWindow.xml",
    "openelec.settings",
    "openelec_logo",
    "service-LibreELEC-Settings-mainWindow.xml",
    "service-OpenELEC-Settings-mainWindow.xml",
)


class PlatformScopeContractTest(unittest.TestCase):
    def test_retired_elec_windows_and_references_are_absent(self):
        for filename in RETIRED_WINDOWS:
            with self.subTest(filename=filename):
                self.assertFalse((SKIN_ROOT / "1080i" / filename).exists())

        for path in sorted(SKIN_ROOT.rglob("*")):
            if not path.is_file() or path.suffix not in {".xml", ".xsp"}:
                continue
            text = path.read_text(encoding="utf-8").lower()
            for reference in RETIRED_REFERENCES:
                with self.subTest(path=path, reference=reference):
                    self.assertNotIn(reference.lower(), text)

    def test_retired_elec_assets_are_not_imported(self):
        binary_manifest = UPSTREAM_ASSETS.read_text(encoding="utf-8")
        self.assertNotIn('"extras/openelec"', binary_manifest)


if __name__ == "__main__":
    unittest.main()
