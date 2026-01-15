"""Steam shortcuts.vdf handling."""

import shutil
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import vdf

from pier.roms.scanner import Game
from pier.steam.paths import find_shortcuts_vdf

# Tag used to identify pier-managed shortcuts
PIER_TAG = "pier"


def generate_app_id(exe: str, name: str) -> int:
    """Generate a deterministic app ID for a non-Steam game.

    Uses CRC32 hash of exe + name, with high bit set to mark as non-Steam.
    This matches the algorithm used by BoilR and Steam ROM Manager.
    """
    combined = exe + name
    crc = zlib.crc32(combined.encode("utf-8")) & 0xFFFFFFFF
    return crc | 0x80000000


def generate_grid_id(app_id: int) -> int:
    """Generate the grid ID used for artwork filenames.

    The grid ID is a 64-bit unsigned value derived from the 32-bit app ID.
    Steam stores app_id as signed, but grid filenames use unsigned.
    """
    unsigned_app_id = app_id & 0xFFFFFFFF
    return (unsigned_app_id << 32) | 0x02000000


def load_shortcuts(path: Path | None = None) -> dict[str, Any]:
    """Load shortcuts from shortcuts.vdf.

    Returns a dict with 'shortcuts' key containing numbered shortcut entries.
    """
    if path is None:
        path = find_shortcuts_vdf()

    if not path or not path.exists():
        return {"shortcuts": {}}

    try:
        data = vdf.binary_loads(path.read_bytes())
        return data
    except Exception:
        return {"shortcuts": {}}


def save_shortcuts(data: dict[str, Any], path: Path | None = None) -> None:
    """Save shortcuts to shortcuts.vdf.

    Creates a backup before writing.
    """
    if path is None:
        path = find_shortcuts_vdf()

    if not path:
        raise RuntimeError("Could not find Steam shortcuts.vdf path")

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing file
    if path.exists():
        backup = path.with_suffix(".vdf.bak")
        shutil.copy(path, backup)

    # Write new file
    path.write_bytes(vdf.binary_dumps(data))


def get_pier_shortcuts(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Get all pier-managed shortcuts, keyed by game ID.

    Returns shortcuts that have the 'pier' tag and a DevkitGameID.
    """
    shortcuts = {}
    for entry in data.get("shortcuts", {}).values():
        if not isinstance(entry, dict):
            continue

        # Check for pier tag
        tags = entry.get("tags", {})
        if isinstance(tags, dict):
            tag_values = list(tags.values())
        else:
            tag_values = []

        if PIER_TAG not in tag_values:
            continue

        # Get game ID from DevkitGameID field
        game_id = entry.get("DevkitGameID", "")
        if game_id:
            shortcuts[game_id] = entry

    return shortcuts


def create_shortcut(game: Game) -> dict[str, Any]:
    """Create a shortcut entry for a game."""
    # Build the launch command
    emulator_parts = game.system.emulator.split()
    exe = f"/run/current-system/sw/bin/{emulator_parts[0]}"

    # Build launch options: core args + ROM path
    launch_args = " ".join(emulator_parts[1:])
    if launch_args:
        launch_options = f'{launch_args} "{game.path}"'
    else:
        launch_options = f'"{game.path}"'

    app_id = generate_app_id(exe, game.name)

    return {
        "appid": app_id,
        "AppName": game.name,
        "Exe": f'"{exe}"',
        "StartDir": f'"{game.path.parent}"',
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": game.id,  # Store our game ID here
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "tags": {"0": PIER_TAG},
    }


@dataclass
class SyncResult:
    """Result of syncing games to Steam."""

    added: list[Game]
    updated: list[Game]
    removed: list[str]  # Game IDs of removed shortcuts
    unchanged: list[Game]


def sync_games(games: list[Game], dry_run: bool = False) -> SyncResult:
    """Sync games to Steam shortcuts.

    Args:
        games: List of games to sync (from disk scan)
        dry_run: If True, don't actually write changes

    Returns:
        SyncResult with details of what changed
    """
    path = find_shortcuts_vdf()
    data = load_shortcuts(path)

    # Get existing pier shortcuts
    existing = get_pier_shortcuts(data)

    # Build set of game IDs we want to sync
    wanted_ids = {g.id for g in games}

    added: list[Game] = []
    updated: list[Game] = []
    unchanged: list[Game] = []
    removed: list[str] = []

    # Check which games need to be added/updated
    for game in games:
        if game.id in existing:
            # Check if shortcut needs updating
            old_shortcut = existing[game.id]
            new_shortcut = create_shortcut(game)

            # Compare key fields
            if (old_shortcut.get("Exe") != new_shortcut["Exe"] or
                old_shortcut.get("LaunchOptions") != new_shortcut["LaunchOptions"] or
                old_shortcut.get("AppName") != new_shortcut["AppName"]):
                updated.append(game)
            else:
                unchanged.append(game)
        else:
            added.append(game)

    # Check which existing shortcuts should be removed (not on disk anymore)
    for game_id in existing:
        if game_id not in wanted_ids:
            removed.append(game_id)

    if dry_run:
        return SyncResult(added, updated, removed, unchanged)

    # Apply changes
    shortcuts = data.get("shortcuts", {})

    # Find the next available index
    existing_indices = [int(k) for k in shortcuts.keys() if k.isdigit()]
    next_index = max(existing_indices, default=-1) + 1

    # Remove old pier shortcuts that are no longer wanted
    keys_to_remove = []
    for key, entry in shortcuts.items():
        if not isinstance(entry, dict):
            continue
        game_id = entry.get("DevkitGameID", "")
        if game_id and game_id in existing and game_id not in wanted_ids:
            keys_to_remove.append(key)

    for key in keys_to_remove:
        del shortcuts[key]

    # Update existing shortcuts
    for game in updated:
        for key, entry in shortcuts.items():
            if isinstance(entry, dict) and entry.get("DevkitGameID") == game.id:
                shortcuts[key] = create_shortcut(game)
                break

    # Add new shortcuts
    for game in added:
        shortcuts[str(next_index)] = create_shortcut(game)
        next_index += 1

    data["shortcuts"] = shortcuts
    save_shortcuts(data, path)

    return SyncResult(added, updated, removed, unchanged)
