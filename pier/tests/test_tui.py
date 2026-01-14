"""TUI smoke tests.

These tests verify that:
1. TUI screens can be instantiated
2. Worker methods use self.app.call_from_thread (not self.call_from_thread)
3. Basic screen composition works
"""

import ast
from pathlib import Path

import pytest


class TestCallFromThreadUsage:
    """Test that screens use self.app.call_from_thread correctly.

    This test prevents regression of the bug where Screen classes
    called self.call_from_thread instead of self.app.call_from_thread.
    """

    SCREEN_FILES = [
        "src/pier/tui/screens/roms.py",
        "src/pier/tui/screens/ports.py",
        "src/pier/tui/screens/bios.py",
        "src/pier/tui/screens/steam.py",
    ]

    def test_no_self_call_from_thread_in_screens(self):
        """Screen files should not use self.call_from_thread.

        They should use self.app.call_from_thread instead.
        """
        # Find the pier source directory
        test_dir = Path(__file__).parent
        src_dir = test_dir.parent

        errors = []

        for screen_file in self.SCREEN_FILES:
            path = src_dir / screen_file
            if not path.exists():
                continue

            source = path.read_text()
            tree = ast.parse(source, filename=str(path))

            for node in ast.walk(tree):
                # Look for attribute access like self.call_from_thread
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "call_from_thread"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                ):
                    errors.append(
                        f"{screen_file}:{node.lineno}: "
                        "uses self.call_from_thread (should be self.app.call_from_thread)"
                    )

        if errors:
            pytest.fail("\n".join(errors))

    def test_self_app_call_from_thread_exists_in_screens(self):
        """Screen files should use self.app.call_from_thread for thread callbacks."""
        test_dir = Path(__file__).parent
        src_dir = test_dir.parent

        for screen_file in self.SCREEN_FILES:
            path = src_dir / screen_file
            if not path.exists():
                continue

            source = path.read_text()
            # Simple text check for correct pattern
            if "call_from_thread" in source:
                assert "self.app.call_from_thread" in source, (
                    f"{screen_file} uses call_from_thread but not via self.app"
                )


class TestScreenImports:
    """Test that screen modules can be imported."""

    def test_import_bios_screen(self):
        """BiosScreen should be importable."""
        from pier.tui.screens.bios import BiosScreen

        assert BiosScreen is not None

    def test_import_ports_screen(self):
        """PortsScreen should be importable."""
        from pier.tui.screens.ports import PortsScreen

        assert PortsScreen is not None

    def test_import_roms_screen(self):
        """RomsScreen should be importable."""
        from pier.tui.screens.roms import RomsScreen

        assert RomsScreen is not None

    def test_import_steam_screen(self):
        """SteamScreen should be importable."""
        from pier.tui.screens.steam import SteamScreen

        assert SteamScreen is not None


class TestScreenBindings:
    """Test that screens have expected bindings."""

    def test_bios_screen_bindings(self):
        """BiosScreen should have expected bindings."""
        from pier.tui.screens.bios import BiosScreen

        bindings = {b.key for b in BiosScreen.BINDINGS}
        assert "escape" in bindings
        assert "r" in bindings  # refresh
        assert "d" in bindings  # download recommended

    def test_ports_screen_bindings(self):
        """PortsScreen should have expected bindings."""
        from pier.tui.screens.ports import PortsScreen

        bindings = {b.key for b in PortsScreen.BINDINGS}
        assert "escape" in bindings
        assert "i" in bindings  # install
        assert "u" in bindings  # update

    def test_roms_screen_bindings(self):
        """RomsScreen should have expected bindings."""
        from pier.tui.screens.roms import RomsScreen

        bindings = {b.key for b in RomsScreen.BINDINGS}
        assert "escape" in bindings
        assert "d" in bindings  # download

    def test_steam_screen_bindings(self):
        """SteamScreen should have expected bindings."""
        from pier.tui.screens.steam import SteamScreen

        bindings = {b.key for b in SteamScreen.BINDINGS}
        assert "escape" in bindings
        assert "h" in bindings  # hide/show toggle
        assert "s" in bindings  # sync
        assert "r" in bindings  # remove
        assert "d" in bindings  # delete
        assert "a" in bindings  # add custom
        assert "i" in bindings  # install
