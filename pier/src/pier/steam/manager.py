"""Steam shortcut manager - manages all non-Steam shortcuts."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pier.steam.artwork import ArtworkStatus, get_artwork_status
from pier.steam.paths import find_shortcuts_vdf
from pier.steam.shortcuts import PIER_TAG, load_shortcuts, save_shortcuts


@dataclass
class Shortcut:
    """A Steam non-Steam shortcut."""

    index: str  # Key in shortcuts dict (e.g., "0", "1")
    app_id: int
    name: str
    exe: str
    start_dir: str
    launch_options: str
    tags: list[str]
    is_pier: bool  # Has the pier tag

    @property
    def display_tags(self) -> str:
        """Tags formatted for display."""
        return ", ".join(self.tags) if self.tags else "-"

    @property
    def artwork(self) -> ArtworkStatus | None:
        """Get artwork status for this shortcut."""
        return get_artwork_status(self.app_id)


def parse_shortcut(index: str, entry: dict[str, Any]) -> Shortcut:
    """Parse a shortcut entry into a Shortcut object."""
    tags_dict = entry.get("tags", {})
    tags = list(tags_dict.values()) if isinstance(tags_dict, dict) else []

    return Shortcut(
        index=index,
        app_id=entry.get("appid", 0),
        name=entry.get("AppName", ""),
        exe=entry.get("Exe", "").strip('"'),
        start_dir=entry.get("StartDir", "").strip('"'),
        launch_options=entry.get("LaunchOptions", ""),
        tags=tags,
        is_pier=PIER_TAG in tags,
    )


def get_all_shortcuts(data: dict[str, Any] | None = None) -> list[Shortcut]:
    """Get all non-Steam shortcuts.

    Args:
        data: Shortcuts data dict, or None to load from disk

    Returns:
        List of Shortcut objects
    """
    if data is None:
        data = load_shortcuts()

    shortcuts = []
    for index, entry in data.get("shortcuts", {}).items():
        if isinstance(entry, dict):
            shortcuts.append(parse_shortcut(index, entry))

    # Sort by index (numerically)
    return sorted(shortcuts, key=lambda s: int(s.index))


def find_shortcut(
    query: str, data: dict[str, Any] | None = None
) -> Shortcut | None:
    """Find a shortcut by index or name.

    Args:
        query: Index number or partial name match
        data: Shortcuts data dict, or None to load from disk

    Returns:
        Matching Shortcut or None
    """
    if data is None:
        data = load_shortcuts()

    shortcuts = get_all_shortcuts(data)

    # Try exact index match first
    if query.isdigit():
        for s in shortcuts:
            if s.index == query:
                return s

    # Try exact name match
    query_lower = query.lower()
    for s in shortcuts:
        if s.name.lower() == query_lower:
            return s

    # Try partial name match
    for s in shortcuts:
        if query_lower in s.name.lower():
            return s

    return None


def remove_shortcut(query: str) -> Shortcut | None:
    """Remove a shortcut by index or name.

    Args:
        query: Index number or name to match

    Returns:
        The removed Shortcut, or None if not found
    """
    path = find_shortcuts_vdf()
    data = load_shortcuts(path)

    shortcut = find_shortcut(query, data)
    if not shortcut:
        return None

    # Remove from data
    del data["shortcuts"][shortcut.index]

    # Re-index shortcuts to keep them sequential
    old_shortcuts = data["shortcuts"]
    data["shortcuts"] = {}
    for new_idx, key in enumerate(sorted(old_shortcuts.keys(), key=int)):
        data["shortcuts"][str(new_idx)] = old_shortcuts[key]

    save_shortcuts(data, path)
    return shortcut


def get_shortcut_details(shortcut: Shortcut) -> dict[str, str]:
    """Get detailed info about a shortcut for display."""
    details = {
        "Index": shortcut.index,
        "Name": shortcut.name,
        "App ID": str(shortcut.app_id),
        "Executable": shortcut.exe,
        "Start Dir": shortcut.start_dir,
        "Launch Options": shortcut.launch_options or "(none)",
        "Tags": shortcut.display_tags,
        "Pier Managed": "Yes" if shortcut.is_pier else "No",
    }

    # Add artwork info
    artwork = shortcut.artwork
    if artwork:
        def _status(has: bool, path: str) -> str:
            if has:
                return f"[green]\\u2713[/green] {path}"
            return "[dim]- (missing)[/dim]"

        details["---"] = ""  # Separator
        details["Artwork"] = ""
        details["  Poster"] = _status(artwork.has_poster, artwork.paths.poster.name)
        details["  Hero"] = _status(artwork.has_hero, artwork.paths.hero.name)
        details["  Logo"] = _status(artwork.has_logo, artwork.paths.logo.name)
        details["  Icon"] = _status(artwork.has_icon, artwork.paths.icon.name)

    return details
