"""Tests for Steam shortcuts handling."""

from pathlib import Path

import vdf

from pier.roms.scanner import Game
from pier.roms.systems import SYSTEMS
from pier.steam.shortcuts import (
    create_shortcut,
    generate_app_id,
    generate_grid_id,
    get_pier_shortcuts,
    load_shortcuts,
    save_shortcuts,
)


class TestAppIdGeneration:
    """Tests for app ID generation."""

    def test_generate_app_id_deterministic(self):
        """App ID should be deterministic for same inputs."""
        app_id1 = generate_app_id("/usr/bin/test", "Test Game")
        app_id2 = generate_app_id("/usr/bin/test", "Test Game")
        assert app_id1 == app_id2

    def test_generate_app_id_different_for_different_inputs(self):
        """App ID should differ for different inputs."""
        app_id1 = generate_app_id("/usr/bin/test", "Game A")
        app_id2 = generate_app_id("/usr/bin/test", "Game B")
        assert app_id1 != app_id2

    def test_generate_app_id_high_bit_set(self):
        """App ID should have high bit set (non-Steam marker)."""
        app_id = generate_app_id("/usr/bin/test", "Test Game")
        assert app_id & 0x80000000 != 0

    def test_generate_grid_id(self):
        """Grid ID should be derived from app ID."""
        app_id = generate_app_id("/usr/bin/test", "Test Game")
        grid_id = generate_grid_id(app_id)

        # Grid ID should be 64-bit with app_id in upper bits
        assert grid_id > 0xFFFFFFFF
        assert (grid_id >> 32) == app_id


class TestShortcuts:
    """Tests for shortcut creation and management."""

    def test_create_shortcut(self, roms_dir: Path):
        """create_shortcut should create valid shortcut dict."""
        game = Game(
            id="rom:n64:Test Game.z64",
            name="Test Game",
            system=SYSTEMS["n64"],
            path=roms_dir / "n64" / "Test Game.z64",
        )

        shortcut = create_shortcut(game)

        assert shortcut["AppName"] == "Test Game"
        assert shortcut["DevkitGameID"] == "rom:n64:Test Game.z64"
        assert shortcut["tags"] == {"0": "pier"}
        assert "retroarch-wrapper" in shortcut["Exe"]
        assert "mupen64plus_next" in shortcut["LaunchOptions"]
        assert "Test Game.z64" in shortcut["LaunchOptions"]

    def test_load_shortcuts_missing_file(self, temp_dir: Path):
        """load_shortcuts should return empty dict for missing file."""
        data = load_shortcuts(temp_dir / "nonexistent.vdf")
        assert data == {"shortcuts": {}}

    def test_save_and_load_shortcuts(self, temp_dir: Path):
        """Shortcuts should round-trip through save/load."""
        path = temp_dir / "shortcuts.vdf"

        data = {
            "shortcuts": {
                "0": {
                    "appid": 12345,
                    "AppName": "Test Game",
                    "Exe": '"/usr/bin/test"',
                    "StartDir": '"/home/user"',
                    "DevkitGameID": "rom:n64:test.z64",
                    "tags": {"0": "pier"},
                }
            }
        }

        save_shortcuts(data, path)
        assert path.exists()

        loaded = load_shortcuts(path)
        assert loaded["shortcuts"]["0"]["AppName"] == "Test Game"
        assert loaded["shortcuts"]["0"]["DevkitGameID"] == "rom:n64:test.z64"

    def test_save_creates_backup(self, temp_dir: Path):
        """save_shortcuts should create backup of existing file."""
        path = temp_dir / "shortcuts.vdf"

        # Write initial file
        initial_data = {"shortcuts": {"0": {"AppName": "Original"}}}
        path.write_bytes(vdf.binary_dumps(initial_data))

        # Save new data
        new_data = {"shortcuts": {"0": {"AppName": "Updated"}}}
        save_shortcuts(new_data, path)

        # Check backup exists
        backup = path.with_suffix(".vdf.bak")
        assert backup.exists()

        # Backup should have original data
        backup_data = vdf.binary_loads(backup.read_bytes())
        assert backup_data["shortcuts"]["0"]["AppName"] == "Original"

    def test_get_pier_shortcuts(self, temp_dir: Path):
        """get_pier_shortcuts should filter by pier tag."""
        data = {
            "shortcuts": {
                "0": {
                    "AppName": "Pier Game",
                    "DevkitGameID": "rom:n64:game1.z64",
                    "tags": {"0": "pier"},
                },
                "1": {
                    "AppName": "Other Game",
                    "DevkitGameID": "",
                    "tags": {"0": "other"},
                },
                "2": {
                    "AppName": "Another Pier Game",
                    "DevkitGameID": "rom:snes:game2.sfc",
                    "tags": {"0": "pier"},
                },
            }
        }

        pier_shortcuts = get_pier_shortcuts(data)

        assert len(pier_shortcuts) == 2
        assert "rom:n64:game1.z64" in pier_shortcuts
        assert "rom:snes:game2.sfc" in pier_shortcuts
        assert pier_shortcuts["rom:n64:game1.z64"]["AppName"] == "Pier Game"
