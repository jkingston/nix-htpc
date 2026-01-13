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
class Library:
    """Tracks installed games and Steam links."""

    installed_ports: dict[str, dict] = field(default_factory=dict)
    downloaded_roms: dict[str, list[str]] = field(default_factory=dict)
    steam_links: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def load(cls, pier_dir: Path | None = None) -> "Library":
        """Load library from disk."""
        if pier_dir is None:
            pier_dir = DEFAULT_EMULATION_DIR / ".pier"
        library_path = pier_dir / "library.json"
        if library_path.exists():
            try:
                data = json.loads(library_path.read_text())
                return cls(
                    installed_ports=data.get("installed_ports", {}),
                    downloaded_roms=data.get("downloaded_roms", {}),
                    steam_links=data.get("steam_links", {}),
                )
            except (json.JSONDecodeError, KeyError):
                # Library file is corrupt or malformed, use empty library
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
            "steam_links": self.steam_links,
        }
        library_path.write_text(json.dumps(data, indent=2))

    def add_port(self, port_id: str, version: str, **kwargs: Any) -> None:
        """Record an installed port."""
        self.installed_ports[port_id] = {
            "version": version,
            **kwargs,
        }

    def add_rom(self, system: str, filename: str) -> None:
        """Record a downloaded ROM."""
        if system not in self.downloaded_roms:
            self.downloaded_roms[system] = []
        if filename not in self.downloaded_roms[system]:
            self.downloaded_roms[system].append(filename)

    def set_steam_link(self, game_id: str, linked: bool) -> None:
        """Set Steam link status for a game."""
        self.steam_links[game_id] = linked

    def is_linked_to_steam(self, game_id: str) -> bool:
        """Check if a game is linked to Steam."""
        return self.steam_links.get(game_id, False)
