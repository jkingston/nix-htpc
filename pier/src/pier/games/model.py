"""Unified game model representing both ROMs and ports."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class GameType(Enum):
    """Type of game."""

    ROM = "rom"
    PORT = "port"


class GameSource(Enum):
    """Where the game is from."""

    LOCAL = "local"  # On disk (ROM in roms_dir or installed port)
    MYRIENT = "myrient"  # Available for download from Myrient
    REGISTRY = "registry"  # Port available in registry but not installed


@dataclass
class Game:
    """A game that can be installed or is available.

    This unified model represents:
    - ROMs found on disk
    - ROMs available on Myrient
    - Ports in the registry (not installed)
    - Ports installed on disk
    """

    id: str  # Unique identifier (e.g., "rom:n64:file.z64" or "port:soh")
    name: str  # Display name
    type: GameType
    source: GameSource
    system: str  # System ID (e.g., "n64", "ps2") or "port" for ports

    # Optional metadata
    description: str = ""
    version: str | None = None

    # Installation status
    installed: bool = False
    in_steam: bool = False
    update_available: bool = False

    # Source-specific data (stored as dict for flexibility)
    metadata: dict = field(default_factory=dict)

    @property
    def is_rom(self) -> bool:
        return self.type == GameType.ROM

    @property
    def is_port(self) -> bool:
        return self.type == GameType.PORT


@dataclass
class InstalledGame:
    """An installed game with full details.

    This is returned when querying a specific installed game,
    providing access to paths and other installation details.
    """

    game: Game
    install_path: Path

    # For ROMs
    rom_path: Path | None = None

    # For ports
    executable_path: Path | None = None
    enhancements_installed: list[str] = field(default_factory=list)
    enhancements_available: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    """Results from a unified game search."""

    # Installed games
    installed_roms: list[Game] = field(default_factory=list)
    installed_ports: list[Game] = field(default_factory=list)

    # Available but not installed
    available_ports: list[Game] = field(default_factory=list)
    available_roms: list[Game] = field(default_factory=list)  # From Myrient

    @property
    def total_installed(self) -> int:
        return len(self.installed_roms) + len(self.installed_ports)

    @property
    def total_available(self) -> int:
        return len(self.available_ports) + len(self.available_roms)

    @property
    def total(self) -> int:
        return self.total_installed + self.total_available

    def all_installable(self) -> list[Game]:
        """Get all games that can be installed (not already installed)."""
        return self.available_ports + self.available_roms

    def all_games(self) -> list[Game]:
        """Get all games in a sensible display order."""
        return (
            self.installed_ports
            + self.installed_roms
            + self.available_ports
            + self.available_roms
        )
