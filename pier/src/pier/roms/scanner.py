"""ROM scanner - finds ROMs on disk."""

import re
from dataclasses import dataclass
from pathlib import Path

from pier.roms.systems import IGNORED_EXTENSIONS, SYSTEMS, System

# Pattern matches parenthesized metadata tags at the end of ROM names
# Handles: (USA), (Europe), (v1.0), (Rev 1), (En,Fr,De), (Unl), (Aftermarket), etc.
_METADATA_PATTERN = re.compile(r"\s*\([^)]+\)\s*$")


def clean_rom_name(name: str) -> str:
    """Clean ROM filename by stripping metadata tags.

    Removes parenthesized suffixes like (USA), (v1.0), (En,Fr,De), etc.

    Args:
        name: ROM filename stem (without extension).

    Returns:
        Cleaned display name.
    """
    cleaned = name
    while True:
        new_cleaned = _METADATA_PATTERN.sub("", cleaned)
        if new_cleaned == cleaned:
            break
        cleaned = new_cleaned
    return cleaned.strip()


@dataclass
class Game:
    """A game found on disk."""

    id: str  # e.g., "rom:n64:Super Mario 64 (USA).z64"
    name: str  # e.g., "Super Mario 64 (USA)" - full filename for matching
    display_name: str  # e.g., "Super Mario 64" - clean name for display
    system: System
    path: Path
    in_steam: bool = False  # Updated by Steam module

    @property
    def filename(self) -> str:
        return self.path.name


def make_game_id(system_id: str, filename: str) -> str:
    """Create a game ID from system and filename."""
    return f"rom:{system_id}:{filename}"


def parse_game_id(game_id: str) -> tuple[str, str] | None:
    """Parse a game ID into (system_id, filename). Returns None if invalid."""
    if not game_id.startswith("rom:"):
        return None
    parts = game_id.split(":", 2)
    if len(parts) != 3:
        return None
    return parts[1], parts[2]


def scan_roms(roms_dir: Path, system_filter: str | None = None) -> list[Game]:
    """Scan ROM directories for games.

    Args:
        roms_dir: Base directory containing system subdirectories
        system_filter: If provided, only scan this system

    Returns:
        List of games found on disk
    """
    games: list[Game] = []

    systems_to_scan = (
        [SYSTEMS[system_filter]] if system_filter and system_filter in SYSTEMS
        else SYSTEMS.values()
    )

    for system in systems_to_scan:
        system_dir = roms_dir / system.id
        if not system_dir.exists():
            continue

        for rom_file in system_dir.iterdir():
            if not rom_file.is_file():
                continue

            # Skip hidden files (dotfiles like .keep, .gitkeep)
            if rom_file.name.startswith("."):
                continue

            ext = rom_file.suffix.lower()
            if ext in IGNORED_EXTENSIONS:
                continue

            # Accept any non-ignored file in the system directory
            # (user may have valid ROMs with unexpected extensions)
            games.append(Game(
                id=make_game_id(system.id, rom_file.name),
                name=rom_file.stem,
                display_name=clean_rom_name(rom_file.stem),
                system=system,
                path=rom_file,
            ))

    return sorted(games, key=lambda g: (g.system.id, g.name.lower()))
