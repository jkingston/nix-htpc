"""Steam directory detection."""

import subprocess
from pathlib import Path


def find_steam_root() -> Path | None:
    """Find the Steam installation directory."""
    candidates = [
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
        Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",  # Flatpak
    ]
    for candidate in candidates:
        if candidate.exists() and (candidate / "steamapps").exists():
            return candidate
    return None


def find_steam_userdata() -> Path | None:
    """Find the Steam userdata directory for the first user found."""
    steam_root = find_steam_root()
    if not steam_root:
        return None

    userdata = steam_root / "userdata"
    if not userdata.exists():
        return None

    # Return first user directory found
    for user_dir in userdata.iterdir():
        if user_dir.is_dir() and user_dir.name.isdigit():
            return user_dir

    return None


def find_shortcuts_vdf() -> Path | None:
    """Find the shortcuts.vdf file path."""
    userdata = find_steam_userdata()
    if not userdata:
        return None
    return userdata / "config" / "shortcuts.vdf"


def find_grid_dir() -> Path | None:
    """Find the Steam grid directory for artwork."""
    userdata = find_steam_userdata()
    if not userdata:
        return None
    grid_dir = userdata / "config" / "grid"
    grid_dir.mkdir(parents=True, exist_ok=True)
    return grid_dir


def is_steam_running() -> bool:
    """Check if Steam process is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "steam"],
            capture_output=True,
        )
        return result.returncode == 0
    except Exception:
        return False
