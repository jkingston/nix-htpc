"""Artwork fetching from SteamGridDB and libretro-thumbnails."""

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import httpx
from PIL import Image

from pier.core.registry import SYSTEMS
from pier.core.constants import HTTP_TIMEOUT, LIBRETRO_THUMBNAILS_BASE
from pier.core.http import AsyncHTTPClient, SteamGridDBClient


@dataclass
class ArtworkSet:
    """A set of artwork images for a game."""

    grid: bytes | None = None  # Vertical grid (600x900)
    hero: bytes | None = None  # Hero banner (1920x620)
    logo: bytes | None = None  # Logo overlay (transparent)
    icon: bytes | None = None  # Icon


# Libretro thumbnail system name mapping
LIBRETRO_SYSTEMS = {
    "n64": "Nintendo - Nintendo 64",
    "snes": "Nintendo - Super Nintendo Entertainment System",
    "nes": "Nintendo - Nintendo Entertainment System",
    "gba": "Nintendo - Game Boy Advance",
    "genesis": "Sega - Mega Drive - Genesis",
    "ps1": "Sony - PlayStation",
    "ps2": "Sony - PlayStation 2",
    "gc": "Nintendo - GameCube",
    "wii": "Nintendo - Wii",
}


class SteamGridDB(SteamGridDBClient):
    """Client for SteamGridDB API."""

    def __init__(self, api_key: str):
        """Initialize with API key.

        Get your key at: https://www.steamgriddb.com/profile/preferences/api
        """
        super().__init__(api_key=api_key)

    async def search_game(self, title: str) -> int | None:
        """Search for a game by title.

        Args:
            title: Game title to search for

        Returns:
            SteamGridDB game ID, or None if not found
        """
        client = await self._get_client()
        url = f"{self.api_base}/search/autocomplete/{quote(title)}"

        try:
            response = await client.get(url)
            if response.status_code != 200:
                return None

            data = response.json()
            if data.get("success") and data.get("data"):
                return data["data"][0]["id"]
        except (httpx.RequestError, KeyError, IndexError):
            # Network error, malformed response, or empty results
            pass

        return None

    async def get_grid(self, game_id: int) -> bytes | None:
        """Get vertical grid image (600x900) for a game.

        Args:
            game_id: SteamGridDB game ID

        Returns:
            Image bytes, or None
        """
        client = await self._get_client()
        url = f"{self.api_base}/grids/game/{game_id}"
        params = {"dimensions": "600x900", "types": "static"}

        try:
            response = await client.get(url, params=params)
            if response.status_code != 200:
                return None

            data = response.json()
            if not data.get("success") or not data.get("data"):
                return None

            image_url = data["data"][0]["url"]
            img_response = await client.get(image_url)
            if img_response.status_code == 200:
                return img_response.content
        except (httpx.RequestError, KeyError, IndexError):
            # Network error, malformed response, or empty results
            pass

        return None

    async def get_hero(self, game_id: int) -> bytes | None:
        """Get hero image (1920x620) for a game."""
        client = await self._get_client()
        url = f"{self.api_base}/heroes/game/{game_id}"

        try:
            response = await client.get(url)
            if response.status_code != 200:
                return None

            data = response.json()
            if not data.get("success") or not data.get("data"):
                return None

            image_url = data["data"][0]["url"]
            img_response = await client.get(image_url)
            if img_response.status_code == 200:
                return img_response.content
        except (httpx.RequestError, KeyError, IndexError):
            # Network error, malformed response, or empty results
            pass

        return None

    async def get_logo(self, game_id: int) -> bytes | None:
        """Get logo image for a game."""
        client = await self._get_client()
        url = f"{self.api_base}/logos/game/{game_id}"

        try:
            response = await client.get(url)
            if response.status_code != 200:
                return None

            data = response.json()
            if not data.get("success") or not data.get("data"):
                return None

            image_url = data["data"][0]["url"]
            img_response = await client.get(image_url)
            if img_response.status_code == 200:
                return img_response.content
        except (httpx.RequestError, KeyError, IndexError):
            # Network error, malformed response, or empty results
            pass

        return None

    async def get_icon(self, game_id: int) -> bytes | None:
        """Get icon image for a game."""
        client = await self._get_client()
        url = f"{self.api_base}/icons/game/{game_id}"

        try:
            response = await client.get(url)
            if response.status_code != 200:
                return None

            data = response.json()
            if not data.get("success") or not data.get("data"):
                return None

            image_url = data["data"][0]["url"]
            img_response = await client.get(image_url)
            if img_response.status_code == 200:
                return img_response.content
        except (httpx.RequestError, KeyError, IndexError):
            # Network error, malformed response, or empty results
            pass

        return None

    async def fetch_artwork(self, title: str) -> ArtworkSet:
        """Fetch all available artwork for a game.

        Args:
            title: Game title to search for

        Returns:
            ArtworkSet with available images
        """
        game_id = await self.search_game(title)
        if not game_id:
            return ArtworkSet()

        # Fetch all artwork types in parallel
        grid, hero, logo, icon = await asyncio.gather(
            self.get_grid(game_id),
            self.get_hero(game_id),
            self.get_logo(game_id),
            self.get_icon(game_id),
        )

        return ArtworkSet(grid=grid, hero=hero, logo=logo, icon=icon)


class LibretroThumbnails(AsyncHTTPClient):
    """Client for libretro-thumbnails repository."""

    def __init__(self):
        super().__init__(timeout=HTTP_TIMEOUT, follow_redirects=True)
        self.base_url = LIBRETRO_THUMBNAILS_BASE

    async def get_boxart(self, system_id: str, rom_name: str) -> bytes | None:
        """Get boxart for a ROM.

        Args:
            system_id: System ID (e.g., "n64", "snes")
            rom_name: ROM filename without extension

        Returns:
            Image bytes, or None
        """
        libretro_system = LIBRETRO_SYSTEMS.get(system_id)
        if not libretro_system:
            return None

        client = await self._get_client()

        # URL encode special characters but preserve spaces
        # libretro uses URL-encoded paths
        encoded_system = quote(libretro_system, safe='')
        encoded_name = quote(rom_name, safe='')

        url = f"{self.base_url}/{encoded_system}/Named_Boxarts/{encoded_name}.png"

        try:
            response = await client.get(url)
            if response.status_code == 200:
                return response.content
        except httpx.RequestError:
            # Network error
            pass

        return None

    async def fetch_artwork(self, system_id: str, rom_name: str) -> ArtworkSet:
        """Fetch artwork for a ROM.

        Note: libretro-thumbnails only has boxart, which we use as the grid image.
        We resize it to Steam's expected dimensions.

        Args:
            system_id: System ID
            rom_name: ROM filename without extension

        Returns:
            ArtworkSet with boxart as grid
        """
        boxart = await self.get_boxart(system_id, rom_name)
        if not boxart:
            return ArtworkSet()

        # Resize boxart to Steam grid dimensions (600x900)
        try:
            resized = resize_image(boxart, 600, 900)
            return ArtworkSet(grid=resized)
        except (OSError, ValueError):
            # PIL couldn't process the image, use original
            return ArtworkSet(grid=boxart)


def resize_image(image_bytes: bytes, width: int, height: int) -> bytes:
    """Resize an image to specified dimensions.

    Args:
        image_bytes: Original image data
        width: Target width
        height: Target height

    Returns:
        Resized image as PNG bytes
    """
    img = Image.open(io.BytesIO(image_bytes))

    # Convert to RGBA if needed
    if img.mode != 'RGBA':
        img = img.convert('RGBA')

    # Calculate scaling to fill the target dimensions
    img_ratio = img.width / img.height
    target_ratio = width / height

    if img_ratio > target_ratio:
        # Image is wider - scale by height
        new_height = height
        new_width = int(height * img_ratio)
    else:
        # Image is taller - scale by width
        new_width = width
        new_height = int(width / img_ratio)

    # Resize with high quality
    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # Crop to center
    left = (new_width - width) // 2
    top = (new_height - height) // 2
    img = img.crop((left, top, left + width, top + height))

    # Save as PNG
    output = io.BytesIO()
    img.save(output, format='PNG')
    return output.getvalue()


class ArtworkManager:
    """Unified artwork manager combining SteamGridDB and libretro-thumbnails."""

    def __init__(self, steamgriddb_api_key: str | None = None):
        """Initialize the manager.

        Args:
            steamgriddb_api_key: Optional SteamGridDB API key
        """
        self.steamgriddb = SteamGridDB(steamgriddb_api_key) if steamgriddb_api_key else None
        self.libretro = LibretroThumbnails()

    async def close(self):
        """Close HTTP clients."""
        if self.steamgriddb:
            await self.steamgriddb.close()
        await self.libretro.close()

    async def fetch_for_port(self, game_name: str) -> ArtworkSet:
        """Fetch artwork for a native port.

        Tries SteamGridDB first (if API key configured).

        Args:
            game_name: Original game name (e.g., "Mario Kart 64")

        Returns:
            ArtworkSet with available images
        """
        if self.steamgriddb:
            artwork = await self.steamgriddb.fetch_artwork(game_name)
            if artwork.grid:
                return artwork

        return ArtworkSet()

    async def fetch_for_rom(self, system_id: str, rom_name: str, game_title: str | None = None) -> ArtworkSet:
        """Fetch artwork for a ROM.

        Tries libretro-thumbnails first, then SteamGridDB as fallback.

        Args:
            system_id: System ID (e.g., "n64")
            rom_name: ROM filename without extension
            game_title: Optional clean game title for SteamGridDB search

        Returns:
            ArtworkSet with available images
        """
        # Try libretro-thumbnails first (exact match by filename)
        artwork = await self.libretro.fetch_artwork(system_id, rom_name)
        if artwork.grid:
            return artwork

        # Fall back to SteamGridDB if available
        if self.steamgriddb and game_title:
            return await self.steamgriddb.fetch_artwork(game_title)

        return ArtworkSet()

    def install_artwork(
        self,
        grid_path: Path,
        grid_id: int,
        artwork: ArtworkSet,
    ):
        """Install artwork files to Steam grid directory.

        Args:
            grid_path: Path to Steam's grid directory
            grid_id: The unsigned appid for filename
            artwork: Artwork to install
        """
        grid_path.mkdir(parents=True, exist_ok=True)

        if artwork.grid:
            (grid_path / f"{grid_id}p.png").write_bytes(artwork.grid)

        if artwork.hero:
            ext = ".png" if artwork.hero[:8] == b'\x89PNG\r\n\x1a\n' else ".jpg"
            (grid_path / f"{grid_id}_hero{ext}").write_bytes(artwork.hero)

        if artwork.logo:
            (grid_path / f"{grid_id}_logo.png").write_bytes(artwork.logo)

        if artwork.icon:
            ext = ".png" if artwork.icon[:8] == b'\x89PNG\r\n\x1a\n' else ".ico"
            (grid_path / f"{grid_id}_icon{ext}").write_bytes(artwork.icon)


# Synchronous wrappers for CLI use
def fetch_artwork_for_port_sync(game_name: str, api_key: str | None = None) -> ArtworkSet:
    """Synchronous wrapper for fetching port artwork."""

    async def _fetch():
        manager = ArtworkManager(api_key)
        try:
            return await manager.fetch_for_port(game_name)
        finally:
            await manager.close()

    return asyncio.run(_fetch())


def fetch_artwork_for_rom_sync(
    system_id: str, rom_name: str, game_title: str | None = None, api_key: str | None = None
) -> ArtworkSet:
    """Synchronous wrapper for fetching ROM artwork."""

    async def _fetch():
        manager = ArtworkManager(api_key)
        try:
            return await manager.fetch_for_rom(system_id, rom_name, game_title)
        finally:
            await manager.close()

    return asyncio.run(_fetch())
