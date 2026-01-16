"""Configuration management for pier."""

import json
from dataclasses import dataclass, field
from pathlib import Path


def _default_roms_dir() -> Path:
    return Path.home() / "Emulation" / "roms"


def _default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "pier"


def _config_dir() -> Path:
    return Path.home() / ".config" / "pier"


def _config_path() -> Path:
    return _config_dir() / "config.json"


@dataclass
class Config:
    """User configuration."""

    roms_dir: Path = field(default_factory=_default_roms_dir)
    data_dir: Path = field(default_factory=_default_data_dir)
    steamgriddb_api_key: str | None = None
    github_token: str | None = None

    @classmethod
    def load(cls) -> "Config":
        """Load config from disk or return defaults."""
        path = _config_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                return cls(
                    roms_dir=Path(data.get("roms_dir", str(_default_roms_dir()))),
                    data_dir=Path(data.get("data_dir", str(_default_data_dir()))),
                    steamgriddb_api_key=data.get("steamgriddb_api_key"),
                    github_token=data.get("github_token"),
                )
            except (json.JSONDecodeError, KeyError):
                pass
        return cls()

    def save(self) -> None:
        """Save config to disk."""
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "roms_dir": str(self.roms_dir),
            "data_dir": str(self.data_dir),
            "steamgriddb_api_key": self.steamgriddb_api_key,
            "github_token": self.github_token,
        }
        path.write_text(json.dumps(data, indent=2))

    def get(self, key: str) -> str | None:
        """Get a config value by key."""
        if key == "roms_dir":
            return str(self.roms_dir)
        elif key == "data_dir":
            return str(self.data_dir)
        elif key == "steamgriddb_api_key":
            return self.steamgriddb_api_key
        elif key == "github_token":
            return self.github_token
        return None

    def set(self, key: str, value: str) -> bool:
        """Set a config value. Returns True if successful."""
        if key == "roms_dir":
            self.roms_dir = Path(value)
            return True
        elif key == "data_dir":
            self.data_dir = Path(value)
            return True
        elif key == "steamgriddb_api_key":
            self.steamgriddb_api_key = value
            return True
        elif key == "github_token":
            self.github_token = value
            return True
        return False
