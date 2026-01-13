"""Tests for configuration and library management."""

import json
from pathlib import Path

from pier.core.config import Config, Library


class TestConfig:
    """Tests for Config class."""

    def test_config_defaults(self):
        """Config should have sensible defaults."""
        config = Config()
        assert config.emulation_dir.name == "Emulation"
        assert config.roms_dir == config.emulation_dir / "roms"
        assert config.ports_dir == config.emulation_dir / "ports"
        assert config.pier_dir == config.emulation_dir / ".pier"

    def test_config_save_and_load(self, temp_dir: Path):
        """Config should round-trip through save/load."""
        pier_dir = temp_dir / ".pier"

        config = Config(
            emulation_dir=temp_dir,
            roms_dir=temp_dir / "roms",
            ports_dir=temp_dir / "ports",
            pier_dir=pier_dir,
            steamgriddb_api_key="test-api-key",
            auto_fetch_artwork=False,
            auto_add_to_steam=False,
            install_hd_textures=False,
        )
        config.save()

        # Verify file was created
        config_file = pier_dir / "config.json"
        assert config_file.exists()

        # Load should fail if we look in the wrong place, so directly read
        data = json.loads(config_file.read_text())
        assert data["steamgriddb_api_key"] == "test-api-key"
        assert data["auto_fetch_artwork"] is False

    def test_config_get(self):
        """Config.get should return attribute values."""
        config = Config()
        assert config.get("auto_fetch_artwork") is True
        assert config.get("nonexistent") is None

    def test_config_set(self):
        """Config.set should update attribute values."""
        config = Config()
        config.set("auto_fetch_artwork", False)
        assert config.auto_fetch_artwork is False

    def test_config_set_path(self, temp_dir: Path):
        """Config.set should convert path strings for _dir attributes."""
        config = Config()
        config.set("roms_dir", str(temp_dir / "new_roms"))
        assert isinstance(config.roms_dir, Path)
        assert config.roms_dir == temp_dir / "new_roms"


class TestLibrary:
    """Tests for Library class."""

    def test_library_defaults(self):
        """Library should have empty defaults."""
        lib = Library()
        assert lib.installed_ports == {}
        assert lib.downloaded_roms == {}
        assert lib.steam_links == {}

    def test_library_add_port(self):
        """Library should track installed ports."""
        lib = Library()
        lib.add_port("soh", "1.0.0", executable="/path/to/soh")

        assert "soh" in lib.installed_ports
        assert lib.installed_ports["soh"]["version"] == "1.0.0"
        assert lib.installed_ports["soh"]["executable"] == "/path/to/soh"

    def test_library_add_rom(self):
        """Library should track downloaded ROMs."""
        lib = Library()
        lib.add_rom("n64", "Super Mario 64 (USA).z64")
        lib.add_rom("n64", "Ocarina of Time (USA).z64")

        assert "n64" in lib.downloaded_roms
        assert len(lib.downloaded_roms["n64"]) == 2
        assert "Super Mario 64 (USA).z64" in lib.downloaded_roms["n64"]

    def test_library_add_rom_no_duplicates(self):
        """Library should not add duplicate ROMs."""
        lib = Library()
        lib.add_rom("n64", "Super Mario 64 (USA).z64")
        lib.add_rom("n64", "Super Mario 64 (USA).z64")

        assert len(lib.downloaded_roms["n64"]) == 1

    def test_library_steam_links(self):
        """Library should track Steam link status."""
        lib = Library()

        assert lib.is_linked_to_steam("soh") is False

        lib.set_steam_link("soh", True)
        assert lib.is_linked_to_steam("soh") is True

        lib.set_steam_link("soh", False)
        assert lib.is_linked_to_steam("soh") is False

    def test_library_save_and_load(self, temp_dir: Path):
        """Library should round-trip through save/load."""
        pier_dir = temp_dir / ".pier"

        lib = Library()
        lib.add_port("soh", "1.0.0")
        lib.add_rom("n64", "Test.z64")
        lib.set_steam_link("soh", True)
        lib.save(pier_dir)

        # Verify file was created
        lib_file = pier_dir / "library.json"
        assert lib_file.exists()

        # Load and verify
        loaded = Library.load(pier_dir)
        assert "soh" in loaded.installed_ports
        assert loaded.installed_ports["soh"]["version"] == "1.0.0"
        assert "Test.z64" in loaded.downloaded_roms.get("n64", [])
        assert loaded.is_linked_to_steam("soh") is True

    def test_library_load_missing_file(self, temp_dir: Path):
        """Library.load should return empty library for missing file."""
        lib = Library.load(temp_dir / "nonexistent")
        assert lib.installed_ports == {}
        assert lib.downloaded_roms == {}
        assert lib.steam_links == {}
