"""Configuration management for pier."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pier.core.constants import DEFAULT_EMULATION_DIR


@dataclass
class Config:
    """User configuration for pier."""

    # Paths
    emulation_dir: Path = field(default_factory=lambda: DEFAULT_EMULATION_DIR)
    roms_dir: Path = field(default_factory=lambda: DEFAULT_EMULATION_DIR / "roms")
    ports_dir: Path = field(default_factory=lambda: DEFAULT_EMULATION_DIR / "ports")
    pier_dir: Path = field(default_factory=lambda: DEFAULT_EMULATION_DIR / ".pier")

    # API keys
    steamgriddb_api_key: str | None = None

    # Preferences
    auto_fetch_artwork: bool = True
    auto_add_to_steam: bool = True
    install_hd_textures: bool = True

    @classmethod
    def load(cls) -> "Config":
        """Load config from disk or return defaults."""
        config_path = DEFAULT_EMULATION_DIR / ".pier" / "config.json"
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text())
                return cls(
                    emulation_dir=Path(data.get("emulation_dir", str(DEFAULT_EMULATION_DIR))),
                    roms_dir=Path(data.get("roms_dir", str(DEFAULT_EMULATION_DIR / "roms"))),
                    ports_dir=Path(data.get("ports_dir", str(DEFAULT_EMULATION_DIR / "ports"))),
                    pier_dir=Path(data.get("pier_dir", str(DEFAULT_EMULATION_DIR / ".pier"))),
                    steamgriddb_api_key=data.get("steamgriddb_api_key"),
                    auto_fetch_artwork=data.get("auto_fetch_artwork", True),
                    auto_add_to_steam=data.get("auto_add_to_steam", True),
                    install_hd_textures=data.get("install_hd_textures", True),
                )
            except (json.JSONDecodeError, KeyError):
                # Config file is corrupt or malformed, use defaults
                pass
        return cls()

    def save(self) -> None:
        """Save config to disk."""
        self.pier_dir.mkdir(parents=True, exist_ok=True)
        config_path = self.pier_dir / "config.json"
        data = {
            "emulation_dir": str(self.emulation_dir),
            "roms_dir": str(self.roms_dir),
            "ports_dir": str(self.ports_dir),
            "pier_dir": str(self.pier_dir),
            "steamgriddb_api_key": self.steamgriddb_api_key,
            "auto_fetch_artwork": self.auto_fetch_artwork,
            "auto_add_to_steam": self.auto_add_to_steam,
            "install_hd_textures": self.install_hd_textures,
        }
        config_path.write_text(json.dumps(data, indent=2))

    def get(self, key: str) -> Any:
        """Get a config value by key."""
        return getattr(self, key, None)

    def set(self, key: str, value: Any) -> None:
        """Set a config value by key."""
        if hasattr(self, key):
            # Handle path conversion
            if key.endswith("_dir"):
                value = Path(value)
            setattr(self, key, value)


@dataclass
class CustomGame:
    """A custom game added by the user."""

    name: str
    executable: str
    start_dir: str
    launch_args: str = ""
    use_steam_run: bool = False  # Wrap with steam-run for Windows exes


@dataclass
class Library:
    """Tracks installed games and Steam state."""

    installed_ports: dict[str, dict] = field(default_factory=dict)
    downloaded_roms: dict[str, list[str]] = field(default_factory=dict)
    hidden_from_steam: set[str] = field(default_factory=set)
    custom_games: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, pier_dir: Path | None = None) -> "Library":
        """Load library from disk."""
        if pier_dir is None:
            pier_dir = DEFAULT_EMULATION_DIR / ".pier"
        library_path = pier_dir / "library.json"
        if library_path.exists():
            try:
                data = json.loads(library_path.read_text())
                # Migrate old steam_links to hidden_from_steam (inverted logic)
                hidden = set(data.get("hidden_from_steam", []))
                if "steam_links" in data and "hidden_from_steam" not in data:
                    # Old format: steam_links = {id: True/False}
                    # True meant "linked", False meant "not linked"
                    # New format: hidden_from_steam = set of IDs to hide
                    # Items NOT in old steam_links default to shown (will add)
                    for game_id, linked in data["steam_links"].items():
                        if not linked:
                            hidden.add(game_id)
                return cls(
                    installed_ports=data.get("installed_ports", {}),
                    downloaded_roms=data.get("downloaded_roms", {}),
                    hidden_from_steam=hidden,
                    custom_games=data.get("custom_games", {}),
                )
            except (json.JSONDecodeError, KeyError):
                pass
        return cls()

    def save(self, pier_dir: Path | None = None) -> None:
        """Save library to disk."""
        if pier_dir is None:
            pier_dir = DEFAULT_EMULATION_DIR / ".pier"
        pier_dir.mkdir(parents=True, exist_ok=True)
        library_path = pier_dir / "library.json"
        data = {
            "installed_ports": self.installed_ports,
            "downloaded_roms": self.downloaded_roms,
            "hidden_from_steam": list(self.hidden_from_steam),
            "custom_games": self.custom_games,
        }
        library_path.write_text(json.dumps(data, indent=2))

    def add_port(self, port_id: str, version: str, **kwargs: Any) -> None:
        """Record an installed port."""
        self.installed_ports[port_id] = {
            "version": version,
            **kwargs,
        }

    def remove_port(self, port_id: str) -> None:
        """Remove an installed port."""
        self.installed_ports.pop(port_id, None)

    def add_rom(self, system: str, filename: str) -> None:
        """Record a downloaded ROM."""
        if system not in self.downloaded_roms:
            self.downloaded_roms[system] = []
        if filename not in self.downloaded_roms[system]:
            self.downloaded_roms[system].append(filename)

    def remove_rom(self, system: str, filename: str) -> None:
        """Remove a downloaded ROM."""
        if system in self.downloaded_roms:
            self.downloaded_roms[system] = [
                r for r in self.downloaded_roms[system] if r != filename
            ]

    def is_hidden_from_steam(self, game_id: str) -> bool:
        """Check if a game is hidden from Steam sync."""
        return game_id in self.hidden_from_steam

    def set_hidden_from_steam(self, game_id: str, hidden: bool) -> None:
        """Set whether a game is hidden from Steam sync."""
        if hidden:
            self.hidden_from_steam.add(game_id)
        else:
            self.hidden_from_steam.discard(game_id)

    def add_custom_game(self, game_id: str, game: CustomGame) -> None:
        """Add a custom game."""
        self.custom_games[game_id] = {
            "name": game.name,
            "executable": game.executable,
            "start_dir": game.start_dir,
            "launch_args": game.launch_args,
            "use_steam_run": game.use_steam_run,
        }

    def remove_custom_game(self, game_id: str) -> None:
        """Remove a custom game."""
        self.custom_games.pop(game_id, None)

    def get_custom_game(self, game_id: str) -> CustomGame | None:
        """Get a custom game by ID."""
        data = self.custom_games.get(game_id)
        if data:
            return CustomGame(
                name=data["name"],
                executable=data["executable"],
                start_dir=data["start_dir"],
                launch_args=data.get("launch_args", ""),
                use_steam_run=data.get("use_steam_run", False),
            )
        return None

    # Backwards compatibility
    def set_steam_link(self, game_id: str, linked: bool) -> None:
        """Deprecated: Use set_hidden_from_steam instead."""
        self.set_hidden_from_steam(game_id, not linked)

    def is_linked_to_steam(self, game_id: str) -> bool:
        """Deprecated: Use is_hidden_from_steam instead."""
        return not self.is_hidden_from_steam(game_id)
