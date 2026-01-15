"""Steam artwork status and management."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pier.steam.paths import find_grid_dir
from pier.steam.vdf import generate_grid_id

# Supported image extensions in order of preference
IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".ico", ".webp"]


class ArtworkType(Enum):
    """Types of Steam artwork with their filename suffixes and SGDB endpoints."""

    POSTER = ("p", "grids")
    HERO = ("_hero", "heroes")
    LOGO = ("_logo", "logos")
    ICON = ("", "icons")  # Icons have no suffix, just the grid_id

    @property
    def suffix(self) -> str:
        return self.value[0]

    @property
    def sgdb_endpoint(self) -> str:
        return self.value[1]


@dataclass
class ArtworkPaths:
    """Paths to artwork files for a shortcut."""

    poster: Path | None = None
    hero: Path | None = None
    logo: Path | None = None
    icon: Path | None = None

    def get(self, key: str) -> Path | None:
        """Dict-like access for compatibility."""
        return getattr(self, key, None)


@dataclass
class ArtworkStatus:
    """Artwork status for a shortcut."""

    grid_id: int
    grid_dir: Path
    has_poster: bool = False
    has_hero: bool = False
    has_logo: bool = False
    has_icon: bool = False
    paths: ArtworkPaths = field(default_factory=ArtworkPaths)

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

    def get_dest_path(self, art_type: ArtworkType, ext: str = ".png") -> Path:
        """Get destination path for downloading artwork."""
        return self.grid_dir / f"{self.grid_id}{art_type.suffix}{ext}"


def find_artwork_file(grid_dir: Path, grid_id: int, suffix: str) -> Path | None:
    """Find existing artwork file with any supported extension.

    Args:
        grid_dir: Steam grid directory
        grid_id: The grid ID for the shortcut
        suffix: Artwork type suffix (e.g., "p", "_hero", "_logo", "")

    Returns:
        Path to existing file or None if not found
    """
    for ext in IMAGE_EXTENSIONS:
        path = grid_dir / f"{grid_id}{suffix}{ext}"
        if path.exists():
            return path
    return None


def get_artwork_status(app_id: int) -> ArtworkStatus | None:
    """Get artwork status for a shortcut.

    Args:
        app_id: The Steam app ID for the shortcut

    Returns:
        ArtworkStatus showing which artwork exists, or None if grid dir not found
    """
    grid_dir = find_grid_dir()
    if not grid_dir:
        return None

    grid_id = generate_grid_id(app_id)

    poster_path = find_artwork_file(grid_dir, grid_id, ArtworkType.POSTER.suffix)
    hero_path = find_artwork_file(grid_dir, grid_id, ArtworkType.HERO.suffix)
    logo_path = find_artwork_file(grid_dir, grid_id, ArtworkType.LOGO.suffix)
    icon_path = find_artwork_file(grid_dir, grid_id, ArtworkType.ICON.suffix)

    return ArtworkStatus(
        grid_id=grid_id,
        grid_dir=grid_dir,
        has_poster=poster_path is not None,
        has_hero=hero_path is not None,
        has_logo=logo_path is not None,
        has_icon=icon_path is not None,
        paths=ArtworkPaths(
            poster=poster_path,
            hero=hero_path,
            logo=logo_path,
            icon=icon_path,
        ),
    )


def clear_artwork(app_id: int) -> int:
    """Remove all artwork files for a shortcut.

    Args:
        app_id: The Steam app ID for the shortcut

    Returns:
        Count of files removed
    """
    status = get_artwork_status(app_id)
    if not status:
        return 0

    removed = 0
    for path in [status.paths.poster, status.paths.hero, status.paths.logo, status.paths.icon]:
        if path and path.exists():
            path.unlink()
            removed += 1

    return removed
