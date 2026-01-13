"""Pytest fixtures for pier tests."""

import tempfile
from pathlib import Path

import pytest

from pier.core.config import Config, Library


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_config(temp_dir: Path) -> Config:
    """Create a Config with temporary paths."""
    emulation_dir = temp_dir / "emulation"
    emulation_dir.mkdir(parents=True)

    return Config(
        emulation_dir=emulation_dir,
        roms_dir=emulation_dir / "roms",
        ports_dir=emulation_dir / "ports",
        pier_dir=emulation_dir / ".pier",
    )


@pytest.fixture
def temp_library() -> Library:
    """Create an empty Library for tests."""
    return Library()
