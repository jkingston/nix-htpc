"""Steam integration module."""

from pier.steam.paths import (
    find_grid_dir,
    find_shortcuts_vdf,
    find_steam_userdata,
    is_steam_running,
)
from pier.steam.sync import get_shortcuts_by_game_id, sync_games
from pier.steam.vdf import (
    generate_app_id,
    generate_grid_id,
    load_shortcuts,
    save_shortcuts,
)

__all__ = [
    "find_grid_dir",
    "find_shortcuts_vdf",
    "find_steam_userdata",
    "is_steam_running",
    "generate_app_id",
    "generate_grid_id",
    "get_shortcuts_by_game_id",
    "load_shortcuts",
    "save_shortcuts",
    "sync_games",
]
