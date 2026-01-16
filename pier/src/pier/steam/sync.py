"""ROM to Steam shortcut synchronization."""

from dataclasses import dataclass
from typing import Any

from pier.roms.scanner import Game
from pier.steam.paths import find_shortcuts_vdf
from pier.steam.shortcuts import PIER_TAG
from pier.steam.vdf import generate_app_id, load_shortcuts, save_shortcuts


def get_pier_shortcuts(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Get all pier-managed shortcuts, keyed by game ID."""
    shortcuts = {}
    for entry in data.get("shortcuts", {}).values():
        if not isinstance(entry, dict):
            continue

        tags = entry.get("tags", {})
        if isinstance(tags, dict):
            tag_values = list(tags.values())
        else:
            tag_values = []

        if PIER_TAG not in tag_values:
            continue

        game_id = entry.get("DevkitGameID", "")
        if game_id:
            shortcuts[game_id] = entry

    return shortcuts


def find_shortcut_by_name(
    shortcuts: dict[str, Any],
    name: str,
) -> tuple[str, dict[str, Any]] | None:
    """Find an existing shortcut by AppName (case-insensitive).

    Args:
        shortcuts: The shortcuts dictionary from VDF data.
        name: The game name to search for.

    Returns:
        Tuple of (index, entry) or None if not found.
    """
    name_lower = name.lower()
    for index, entry in shortcuts.items():
        if isinstance(entry, dict):
            if entry.get("AppName", "").lower() == name_lower:
                return (index, entry)
    return None


def create_shortcut(game: Game) -> dict[str, Any]:
    """Create a shortcut entry for a game."""
    emulator_parts = game.system.emulator.split()
    exe = f"/run/current-system/sw/bin/{emulator_parts[0]}"

    launch_args = " ".join(emulator_parts[1:])
    if launch_args:
        launch_options = f'{launch_args} "{game.path}"'
    else:
        launch_options = f'"{game.path}"'

    app_id = generate_app_id(exe, game.name)

    return {
        "appid": app_id,
        "AppName": game.display_name,
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
        "DevkitGameID": game.id,
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
    removed: list[str]
    unchanged: list[Game]
    adopted: list[Game]


def sync_games(games: list[Game], dry_run: bool = False) -> SyncResult:
    """Sync games to Steam shortcuts."""
    path = find_shortcuts_vdf()
    data = load_shortcuts(path)

    existing = get_pier_shortcuts(data)
    wanted_ids = {g.id for g in games}

    added: list[Game] = []
    updated: list[Game] = []
    unchanged: list[Game] = []
    removed: list[str] = []
    adopted: list[Game] = []

    for game in games:
        if game.id in existing:
            old_shortcut = existing[game.id]
            new_shortcut = create_shortcut(game)

            if (old_shortcut.get("Exe") != new_shortcut["Exe"] or
                old_shortcut.get("LaunchOptions") != new_shortcut["LaunchOptions"] or
                old_shortcut.get("AppName") != new_shortcut["AppName"]):
                updated.append(game)
            else:
                unchanged.append(game)
        else:
            added.append(game)

    for game_id in existing:
        if game_id not in wanted_ids:
            removed.append(game_id)

    if dry_run:
        # For dry run, check what would be adopted vs truly added
        shortcuts = data.get("shortcuts", {})
        for game in added[:]:
            match = find_shortcut_by_name(shortcuts, game.name)
            if match:
                adopted.append(game)
                added.remove(game)
        return SyncResult(added, updated, removed, unchanged, adopted)

    shortcuts = data.get("shortcuts", {})
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

    # Add or adopt shortcuts
    for game in added[:]:
        match = find_shortcut_by_name(shortcuts, game.name)
        if match:
            # Adopt existing shortcut
            index, existing_entry = match
            new_shortcut = create_shortcut(game)
            # Preserve LastPlayTime from existing shortcut
            new_shortcut["LastPlayTime"] = existing_entry.get("LastPlayTime", 0)
            shortcuts[index] = new_shortcut
            adopted.append(game)
            added.remove(game)
        else:
            # Create new shortcut
            shortcuts[str(next_index)] = create_shortcut(game)
            next_index += 1

    data["shortcuts"] = shortcuts
    save_shortcuts(data, path)

    return SyncResult(added, updated, removed, unchanged, adopted)
