"""Steam shortcut management for PC ports."""

from pathlib import Path
from typing import Any

from pier.ports.registry import Port
from pier.steam.paths import find_shortcuts_vdf
from pier.steam.shortcuts import PIER_TAG
from pier.steam.vdf import generate_app_id, load_shortcuts, save_shortcuts

PORT_TAG = "port"


def find_port_executable(port: Port, install_dir: Path) -> Path:
    """Find the executable for a port.

    Args:
        port: The port definition.
        install_dir: Directory where the port is installed.

    Returns:
        Path to the executable (may not exist).
    """
    executable = install_dir / port.executable_name
    if not executable.exists():
        # Try to find in subdirectory
        for subdir in install_dir.iterdir():
            if subdir.is_dir():
                candidate = subdir / port.executable_name
                if candidate.exists():
                    return candidate
    return executable


def get_expected_port_values(port: Port, install_dir: Path) -> tuple[str, str, str]:
    """Get the expected exe, start_dir, and launch_options for a port.

    Args:
        port: The port definition.
        install_dir: Directory where the port is installed.

    Returns:
        Tuple of (exe, start_dir, launch_options) without quotes.
    """
    executable = find_port_executable(port, install_dir)
    return (str(executable), str(executable.parent), port.launch_args or "")


def create_port_shortcut(port: Port, install_dir: Path) -> dict[str, Any]:
    """Create a Steam shortcut entry for a port.

    Args:
        port: The port definition.
        install_dir: Directory where the port is installed.

    Returns:
        Shortcut dictionary for Steam's shortcuts.vdf.
    """
    exe, start_dir, launch_options = get_expected_port_values(port, install_dir)
    app_id = generate_app_id(exe, port.name)

    shortcut = {
        "appid": app_id,
        "AppName": port.name,
        "Exe": f'"{exe}"',
        "StartDir": f'"{start_dir}"',
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": f"port:{port.id}",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "tags": {"0": PIER_TAG, "1": PORT_TAG},
    }

    return shortcut


def get_port_shortcuts(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Get all pier-managed port shortcuts, keyed by port ID.

    Args:
        data: Loaded shortcuts.vdf data.

    Returns:
        Dict mapping port ID to shortcut entry.
    """
    shortcuts = {}
    for entry in data.get("shortcuts", {}).values():
        if not isinstance(entry, dict):
            continue

        tags = entry.get("tags", {})
        if isinstance(tags, dict):
            tag_values = list(tags.values())
        else:
            tag_values = []

        # Must have both pier and port tags
        if PIER_TAG not in tag_values or PORT_TAG not in tag_values:
            continue

        game_id = entry.get("DevkitGameID", "")
        if game_id and game_id.startswith("port:"):
            port_id = game_id[5:]  # Remove "port:" prefix
            shortcuts[port_id] = entry

    return shortcuts


def sync_port_to_steam(port: Port, install_dir: Path) -> int:
    """Add or update a Steam shortcut for a port.

    Args:
        port: The port to sync.
        install_dir: Directory where the port is installed.

    Returns:
        The app ID of the shortcut.
    """
    path = find_shortcuts_vdf()
    data = load_shortcuts(path)

    existing = get_port_shortcuts(data)
    new_shortcut = create_port_shortcut(port, install_dir)
    app_id = new_shortcut["appid"]

    shortcuts = data.get("shortcuts", {})

    if port.id in existing:
        # Update existing shortcut
        for key, entry in shortcuts.items():
            if isinstance(entry, dict) and entry.get("DevkitGameID") == f"port:{port.id}":
                shortcuts[key] = new_shortcut
                break
    else:
        # Add new shortcut
        existing_indices = [int(k) for k in shortcuts.keys() if k.isdigit()]
        next_index = max(existing_indices, default=-1) + 1
        shortcuts[str(next_index)] = new_shortcut

    data["shortcuts"] = shortcuts
    save_shortcuts(data, path)

    return app_id


def remove_port_from_steam(port_id: str) -> bool:
    """Remove a port's Steam shortcut.

    Args:
        port_id: The port ID to remove.

    Returns:
        True if a shortcut was removed, False if not found.
    """
    path = find_shortcuts_vdf()
    data = load_shortcuts(path)

    shortcuts = data.get("shortcuts", {})
    key_to_remove = None

    for key, entry in shortcuts.items():
        if isinstance(entry, dict) and entry.get("DevkitGameID") == f"port:{port_id}":
            key_to_remove = key
            break

    if key_to_remove is None:
        return False

    del shortcuts[key_to_remove]
    data["shortcuts"] = shortcuts
    save_shortcuts(data, path)

    return True


def is_port_in_steam(port_id: str) -> bool:
    """Check if a port has a Steam shortcut.

    Args:
        port_id: The port ID to check.

    Returns:
        True if the port has a Steam shortcut.
    """
    path = find_shortcuts_vdf()
    if not path or not path.exists():
        return False

    data = load_shortcuts(path)
    existing = get_port_shortcuts(data)
    return port_id in existing
