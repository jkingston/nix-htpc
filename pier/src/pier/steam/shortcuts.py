"""Steam shortcut management."""

from dataclasses import dataclass
from typing import Any

from pier.steam.artwork import ArtworkStatus, get_artwork_status
from pier.steam.paths import find_shortcuts_vdf
from pier.steam.vdf import load_shortcuts, save_shortcuts

PIER_TAG = "pier"


@dataclass
class Shortcut:
    """A Steam non-Steam shortcut."""

    index: str
    app_id: int
    name: str
    exe: str
    start_dir: str
    launch_options: str
    tags: list[str]
    is_pier: bool

    @property
    def display_tags(self) -> str:
        return ", ".join(self.tags) if self.tags else "-"

    @property
    def artwork(self) -> ArtworkStatus | None:
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
    """Get all non-Steam shortcuts."""
    if data is None:
        data = load_shortcuts()

    shortcuts = []
    for index, entry in data.get("shortcuts", {}).items():
        if isinstance(entry, dict):
            shortcuts.append(parse_shortcut(index, entry))

    return sorted(shortcuts, key=lambda s: int(s.index))


def find_shortcut(query: str, data: dict[str, Any] | None = None) -> Shortcut | None:
    """Find a shortcut by index or name."""
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
    """Remove a shortcut by index or name."""
    path = find_shortcuts_vdf()
    data = load_shortcuts(path)

    shortcut = find_shortcut(query, data)
    if not shortcut:
        return None

    del data["shortcuts"][shortcut.index]

    # Re-index shortcuts to keep them sequential
    old_shortcuts = data["shortcuts"]
    data["shortcuts"] = {}
    for new_idx, key in enumerate(sorted(old_shortcuts.keys(), key=int)):
        data["shortcuts"][str(new_idx)] = old_shortcuts[key]

    save_shortcuts(data, path)
    return shortcut


class ShortcutStatus:
    """Status constants for shortcuts."""

    READY = "ready"
    NEEDS_SYNC = "needs_sync"
    BROKEN = "broken"
    UPDATE_AVAILABLE = "update_available"


def shortcut_matches(
    shortcut: Shortcut,
    expected_exe: str,
    expected_start_dir: str,
    expected_launch_options: str,
) -> bool:
    """Check if a shortcut matches expected values.

    Compares the functional parts of a shortcut (exe, start_dir, launch_options)
    to detect if it needs to be re-synced. Does not compare cosmetic fields
    like display name or icon.

    Args:
        shortcut: The existing shortcut to check.
        expected_exe: Expected executable path (without quotes).
        expected_start_dir: Expected start directory (without quotes).
        expected_launch_options: Expected launch options.

    Returns:
        True if the shortcut matches the expected values.
    """
    return (
        shortcut.exe == expected_exe
        and shortcut.start_dir == expected_start_dir
        and shortcut.launch_options == expected_launch_options
    )


def get_shortcut_status(
    shortcut: Shortcut,
    file_exists: bool,
    expected_exe: str | None = None,
    expected_start_dir: str | None = None,
    expected_launch_options: str | None = None,
    update_available: bool = False,
) -> str:
    """Determine the status of a shortcut.

    Args:
        shortcut: The shortcut to check.
        file_exists: Whether the target file (ROM or port executable) exists.
        expected_exe: Expected executable path (if checking staleness).
        expected_start_dir: Expected start directory (if checking staleness).
        expected_launch_options: Expected launch options (if checking staleness).
        update_available: Whether an update is available (for ports).

    Returns:
        One of: 'ready', 'needs_sync', 'broken', 'update_available'
    """
    if not file_exists:
        return ShortcutStatus.BROKEN

    # If we have expected values, check for staleness
    if expected_exe is not None:
        if not shortcut_matches(
            shortcut, expected_exe, expected_start_dir or "", expected_launch_options or ""
        ):
            return ShortcutStatus.NEEDS_SYNC

    if update_available:
        return ShortcutStatus.UPDATE_AVAILABLE

    return ShortcutStatus.READY


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

    artwork = shortcut.artwork
    if artwork:
        def _status(has: bool, path) -> str:
            if has:
                return f"[green]\\u2713[/green] {path.name if path else 'yes'}"
            return "[dim]- (missing)[/dim]"

        details["---"] = ""
        details["Artwork"] = ""
        details["  Poster"] = _status(artwork.has_poster, artwork.paths.get("poster"))
        details["  Hero"] = _status(artwork.has_hero, artwork.paths.get("hero"))
        details["  Logo"] = _status(artwork.has_logo, artwork.paths.get("logo"))
        details["  Icon"] = _status(artwork.has_icon, artwork.paths.get("icon"))

    return details
