"""Tests for ROM scanner."""

from pathlib import Path

from pier.roms.scanner import make_game_id, parse_game_id, scan_roms
from pier.roms.systems import SYSTEMS


class TestGameId:
    """Tests for game ID functions."""

    def test_make_game_id(self):
        """make_game_id should create correct format."""
        game_id = make_game_id("n64", "Super Mario 64 (USA).z64")
        assert game_id == "rom:n64:Super Mario 64 (USA).z64"

    def test_parse_game_id(self):
        """parse_game_id should extract system and filename."""
        result = parse_game_id("rom:n64:Super Mario 64 (USA).z64")
        assert result == ("n64", "Super Mario 64 (USA).z64")

    def test_parse_game_id_invalid(self):
        """parse_game_id should return None for invalid IDs."""
        assert parse_game_id("invalid") is None
        assert parse_game_id("port:soh") is None
        assert parse_game_id("rom:n64") is None


class TestScanRoms:
    """Tests for ROM scanning."""

    def test_scan_finds_roms(self, roms_dir: Path):
        """scan_roms should find ROMs in system directories."""
        games = scan_roms(roms_dir)

        assert len(games) == 4  # 2 N64 + 1 SNES + 1 PS2
        names = [g.name for g in games]
        assert "Super Mario 64 (USA)" in names
        assert "Mario Kart 64 (USA)" in names
        assert "Super Mario World (USA)" in names
        assert "Gran Turismo 4 (USA)" in names

    def test_scan_ignores_txt_files(self, roms_dir: Path):
        """scan_roms should ignore .txt files."""
        games = scan_roms(roms_dir)
        filenames = [g.filename for g in games]
        assert "readme.txt" not in filenames

    def test_scan_ignores_hidden_files(self, roms_dir: Path):
        """scan_roms should ignore hidden files (dotfiles)."""
        # Create a .keep file in n64 directory
        keep_file = roms_dir / "n64" / ".keep"
        keep_file.touch()

        games = scan_roms(roms_dir)
        filenames = [g.filename for g in games]
        assert ".keep" not in filenames
        names = [g.name for g in games]
        assert ".keep" not in names

    def test_scan_with_system_filter(self, roms_dir: Path):
        """scan_roms should filter by system."""
        n64_games = scan_roms(roms_dir, system_filter="n64")
        assert len(n64_games) == 2
        assert all(g.system.id == "n64" for g in n64_games)

        ps2_games = scan_roms(roms_dir, system_filter="ps2")
        assert len(ps2_games) == 1
        assert ps2_games[0].name == "Gran Turismo 4 (USA)"

    def test_scan_empty_dir(self, temp_dir: Path):
        """scan_roms should handle empty directory."""
        games = scan_roms(temp_dir)
        assert games == []

    def test_game_properties(self, roms_dir: Path):
        """Game objects should have correct properties."""
        games = scan_roms(roms_dir, system_filter="n64")
        game = next(g for g in games if "Mario 64" in g.name)

        assert game.id == "rom:n64:Super Mario 64 (USA).z64"
        assert game.name == "Super Mario 64 (USA)"
        assert game.filename == "Super Mario 64 (USA).z64"
        assert game.system == SYSTEMS["n64"]
        assert game.path == roms_dir / "n64" / "Super Mario 64 (USA).z64"
        assert game.in_steam is False  # Default
