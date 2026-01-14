"""Tests for Steam shortcut management."""

import tempfile
from pathlib import Path

from pier.core.steam import (
    Shortcut,
    dict_to_shortcut,
    generate_appid,
    generate_grid_id,
    load_shortcuts,
    save_shortcuts,
    shortcut_to_dict,
)


class TestAppIdGeneration:
    """Tests for Steam appid generation."""

    def test_generate_appid_consistent(self):
        """generate_appid should return same ID for same inputs."""
        id1 = generate_appid('"/path/to/game"', "Test Game")
        id2 = generate_appid('"/path/to/game"', "Test Game")
        assert id1 == id2

    def test_generate_appid_different_for_different_inputs(self):
        """generate_appid should return different IDs for different inputs."""
        id1 = generate_appid('"/path/to/game1"', "Game 1")
        id2 = generate_appid('"/path/to/game2"', "Game 2")
        assert id1 != id2

    def test_generate_appid_is_unsigned_with_high_bit(self):
        """generate_appid should return an unsigned 32-bit integer with high bit set."""
        appid = generate_appid('"/path/to/game"', "Test Game")
        # Should be unsigned 32-bit
        assert 0 <= appid <= 0xFFFFFFFF
        # High bit should be set (marks as non-Steam game)
        assert appid & 0x80000000

    def test_generate_grid_id_consistent(self):
        """generate_grid_id should return same ID for same inputs."""
        id1 = generate_grid_id('"/path/to/game"', "Test Game")
        id2 = generate_grid_id('"/path/to/game"', "Test Game")
        assert id1 == id2

    def test_generate_grid_id_is_unsigned(self):
        """generate_grid_id should return an unsigned ID for filenames."""
        grid_id = generate_grid_id('"/path/to/game"', "Test Game")
        assert grid_id >= 0


class TestVDFReadWrite:
    """Tests for VDF binary format reading/writing."""

    def test_vdf_roundtrip(self):
        """Writing and reading VDF should preserve data."""
        shortcuts = {
            "0": {
                "appid": 12345,
                "AppName": "Test Game",
                "Exe": '"/path/to/game"',
                "StartDir": '"/path/to"',
                "tags": {"0": "TestTag"},
            }
        }

        with tempfile.NamedTemporaryFile(delete=False, suffix=".vdf") as f:
            path = Path(f.name)

        try:
            save_shortcuts(shortcuts, path)
            loaded = load_shortcuts(path)

            assert "0" in loaded
            assert loaded["0"]["AppName"] == "Test Game"
            assert loaded["0"]["appid"] == 12345
        finally:
            path.unlink()

    def test_load_nonexistent_file(self):
        """load_shortcuts should return empty dict for missing file."""
        result = load_shortcuts(Path("/nonexistent/file.vdf"))
        assert result == {}


class TestShortcut:
    """Tests for Shortcut dataclass."""

    def test_shortcut_grid_id(self):
        """Shortcut.grid_id should match generate_grid_id."""
        shortcut = Shortcut(
            appid=12345,
            app_name="Test Game",
            exe='"/path/to/game"',
            start_dir='"/path/to"',
        )
        expected = generate_grid_id('"/path/to/game"', "Test Game")
        assert shortcut.grid_id == expected

    def test_shortcut_to_dict(self):
        """shortcut_to_dict should create valid dict."""
        shortcut = Shortcut(
            appid=12345,
            app_name="Test Game",
            exe='"/path/to/game"',
            start_dir='"/path/to"',
            tags=["Tag1", "Tag2"],
        )
        d = shortcut_to_dict(shortcut)

        assert d["appid"] == 12345
        assert d["AppName"] == "Test Game"
        assert d["Exe"] == '"/path/to/game"'
        assert d["tags"] == {"0": "Tag1", "1": "Tag2"}

    def test_dict_to_shortcut(self):
        """dict_to_shortcut should create valid Shortcut."""
        d = {
            "appid": 12345,
            "AppName": "Test Game",
            "Exe": '"/path/to/game"',
            "StartDir": '"/path/to"',
            "tags": {"0": "Tag1", "1": "Tag2"},
        }
        shortcut = dict_to_shortcut(d)

        assert shortcut.appid == 12345
        assert shortcut.app_name == "Test Game"
        assert shortcut.tags == ["Tag1", "Tag2"]

    def test_shortcut_roundtrip(self):
        """Converting Shortcut to dict and back should preserve data."""
        original = Shortcut(
            appid=12345,
            app_name="Test Game",
            exe='"/path/to/game"',
            start_dir='"/path/to"',
            launch_options="--fullscreen",
            tags=["TestTag"],
            is_hidden=True,
        )
        d = shortcut_to_dict(original)
        restored = dict_to_shortcut(d)

        assert restored.appid == original.appid
        assert restored.app_name == original.app_name
        assert restored.exe == original.exe
        assert restored.launch_options == original.launch_options
        assert restored.tags == original.tags
        assert restored.is_hidden == original.is_hidden
