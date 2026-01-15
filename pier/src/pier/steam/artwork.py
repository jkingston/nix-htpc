"""Steam artwork status and management."""

from dataclasses import dataclass
from pathlib import Path

from pier.steam.paths import find_grid_dir
from pier.steam.shortcuts import generate_grid_id


@dataclass
class ArtworkPaths:
    """Paths to artwork files for a shortcut."""

    poster: Path
    hero: Path
    logo: Path
    icon: Path


@dataclass
class ArtworkStatus:
    """Artwork status for a shortcut."""

    grid_id: int
    has_poster: bool
    has_hero: bool
    has_logo: bool
    has_icon: bool
    paths: ArtworkPaths

    @property
    def complete(self) -> bool:
        """Check if all artwork types are present."""
        return self.has_poster and self.has_hero and self.has_logo and self.has_icon

    @property
    def count(self) -> int:
        """Count of artwork types present."""
        return sum([self.has_poster, self.has_hero, self.has_logo, self.has_icon])

    @property
    def missing(self) -> list[str]:
        """List of missing artwork types."""
        result = []
        if not self.has_poster:
            result.append("poster")
        if not self.has_hero:
            result.append("hero")
        if not self.has_logo:
            result.append("logo")
        if not self.has_icon:
            result.append("icon")
        return result


def get_artwork_paths(app_id: int) -> ArtworkPaths | None:
    """Get expected paths for all artwork types.

    Args:
        app_id: The Steam app ID for the shortcut

    Returns:
        ArtworkPaths with full paths for each artwork type, or None if grid dir not found
    """
    grid_dir = find_grid_dir()
    if not grid_dir:
        return None

    grid_id = generate_grid_id(app_id)

    return ArtworkPaths(
        poster=grid_dir / f"{grid_id}p.png",
        hero=grid_dir / f"{grid_id}_hero.png",
        logo=grid_dir / f"{grid_id}_logo.png",
        icon=grid_dir / f"{grid_id}.ico",
    )


def get_artwork_status(app_id: int) -> ArtworkStatus | None:
    """Get artwork status for a shortcut.

    Args:
        app_id: The Steam app ID for the shortcut

    Returns:
        ArtworkStatus showing which artwork exists, or None if grid dir not found
    """
    paths = get_artwork_paths(app_id)
    if not paths:
        return None

    grid_id = generate_grid_id(app_id)

    return ArtworkStatus(
        grid_id=grid_id,
        has_poster=paths.poster.exists(),
        has_hero=paths.hero.exists(),
        has_logo=paths.logo.exists(),
        has_icon=paths.icon.exists(),
        paths=paths,
    )


def clear_artwork(app_id: int) -> int:
    """Remove all artwork files for a shortcut.

    Args:
        app_id: The Steam app ID for the shortcut

    Returns:
        Count of files removed
    """
    paths = get_artwork_paths(app_id)
    if not paths:
        return 0

    removed = 0
    for path in [paths.poster, paths.hero, paths.logo, paths.icon]:
        if path.exists():
            path.unlink()
            removed += 1

    return removed
