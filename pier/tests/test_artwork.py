"""Tests for Steam artwork management."""

from pathlib import Path
from unittest.mock import patch

from pier.steam.artwork import (
    ArtworkType,
    clear_artwork,
    get_artwork_status,
)
from pier.steam.vdf import generate_grid_id


class TestArtworkType:
    """Tests for ArtworkType enum."""

    def test_artwork_type_suffixes(self):
        """ArtworkType should have correct suffixes."""
        assert ArtworkType.POSTER.suffix == "p"
        assert ArtworkType.HERO.suffix == "_hero"
        assert ArtworkType.LOGO.suffix == "_logo"
        assert ArtworkType.ICON.suffix == ""

    def test_artwork_type_sgdb_endpoints(self):
        """ArtworkType should have correct SGDB endpoints."""
        assert ArtworkType.POSTER.sgdb_endpoint == "grids"
        assert ArtworkType.HERO.sgdb_endpoint == "heroes"
        assert ArtworkType.LOGO.sgdb_endpoint == "logos"
        assert ArtworkType.ICON.sgdb_endpoint == "icons"


class TestArtworkStatus:
    """Tests for artwork status checking."""

    def test_get_artwork_status_all_missing(self, temp_dir: Path):
        """get_artwork_status should detect missing artwork."""
        grid_dir = temp_dir / "grid"
        grid_dir.mkdir()

        with patch("pier.steam.artwork.find_grid_dir", return_value=grid_dir):
            status = get_artwork_status(12345)

            assert status is not None
            assert status.has_poster is False
            assert status.has_hero is False
            assert status.has_logo is False
            assert status.has_icon is False
            assert status.complete is False
            assert status.count == 0
            assert set(status.missing) == {"poster", "hero", "logo", "icon"}

    def test_get_artwork_status_some_present(self, temp_dir: Path):
        """get_artwork_status should detect present artwork."""
        grid_dir = temp_dir / "grid"
        grid_dir.mkdir()

        with patch("pier.steam.artwork.find_grid_dir", return_value=grid_dir):
            app_id = 12345
            grid_id = generate_grid_id(app_id)

            # Create poster and logo
            (grid_dir / f"{grid_id}p.png").touch()
            (grid_dir / f"{grid_id}_logo.png").touch()

            status = get_artwork_status(app_id)

            assert status is not None
            assert status.has_poster is True
            assert status.has_hero is False
            assert status.has_logo is True
            assert status.has_icon is False
            assert status.complete is False
            assert status.count == 2
            assert set(status.missing) == {"hero", "icon"}

    def test_get_artwork_status_all_present(self, temp_dir: Path):
        """get_artwork_status should detect complete artwork."""
        grid_dir = temp_dir / "grid"
        grid_dir.mkdir()

        with patch("pier.steam.artwork.find_grid_dir", return_value=grid_dir):
            app_id = 12345
            grid_id = generate_grid_id(app_id)

            # Create all artwork
            (grid_dir / f"{grid_id}p.png").touch()
            (grid_dir / f"{grid_id}_hero.png").touch()
            (grid_dir / f"{grid_id}_logo.png").touch()
            (grid_dir / f"{grid_id}.ico").touch()

            status = get_artwork_status(app_id)

            assert status is not None
            assert status.complete is True
            assert status.count == 4
            assert status.missing == []


class TestClearArtwork:
    """Tests for clearing artwork."""

    def test_clear_artwork_removes_files(self, temp_dir: Path):
        """clear_artwork should remove existing artwork files."""
        grid_dir = temp_dir / "grid"
        grid_dir.mkdir()

        with patch("pier.steam.artwork.find_grid_dir", return_value=grid_dir):
            app_id = 12345
            grid_id = generate_grid_id(app_id)

            # Create artwork files
            poster = grid_dir / f"{grid_id}p.png"
            hero = grid_dir / f"{grid_id}_hero.png"
            poster.touch()
            hero.touch()

            removed = clear_artwork(app_id)

            assert removed == 2
            assert not poster.exists()
            assert not hero.exists()

    def test_clear_artwork_returns_zero_if_none(self, temp_dir: Path):
        """clear_artwork should return 0 if no files to remove."""
        grid_dir = temp_dir / "grid"
        grid_dir.mkdir()

        with patch("pier.steam.artwork.find_grid_dir", return_value=grid_dir):
            removed = clear_artwork(12345)
            assert removed == 0
