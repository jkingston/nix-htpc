"""Test fixtures."""

from pathlib import Path

import pytest


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for tests."""
    return tmp_path


@pytest.fixture
def roms_dir(tmp_path: Path) -> Path:
    """Create a temporary ROMs directory with sample files."""
    roms = tmp_path / "roms"

    # Create N64 ROMs
    n64_dir = roms / "n64"
    n64_dir.mkdir(parents=True)
    (n64_dir / "Super Mario 64 (USA).z64").touch()
    (n64_dir / "Mario Kart 64 (USA).z64").touch()
    (n64_dir / "readme.txt").touch()  # Should be ignored

    # Create SNES ROMs
    snes_dir = roms / "snes"
    snes_dir.mkdir(parents=True)
    (snes_dir / "Super Mario World (USA).sfc").touch()

    # Create PS2 ROMs
    ps2_dir = roms / "ps2"
    ps2_dir.mkdir(parents=True)
    (ps2_dir / "Gran Turismo 4 (USA).iso").touch()

    return roms
