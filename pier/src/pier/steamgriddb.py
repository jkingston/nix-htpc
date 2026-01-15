"""SteamGridDB API client for fetching game artwork."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass
class SteamGridDBGame:
    """A game result from SteamGridDB."""

    id: int
    name: str
    release_date: int | None = None


@dataclass
class SteamGridDBImage:
    """An image result from SteamGridDB."""

    id: int
    url: str
    thumb: str
    width: int
    height: int
    style: str | None = None


class SteamGridDBError(Exception):
    """Error from SteamGridDB API."""

    pass


class SteamGridDBClient:
    """Client for the SteamGridDB API."""

    BASE_URL = "https://www.steamgriddb.com/api/v2"

    def __init__(self, api_key: str):
        """Initialize the client with an API key.

        Get your API key at: https://www.steamgriddb.com/profile/preferences/api
        """
        self.api_key = api_key
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    def _request(self, path: str) -> dict[str, Any]:
        """Make an API request."""
        try:
            response = self._client.get(path)
            response.raise_for_status()
            data = response.json()

            if not data.get("success", False):
                raise SteamGridDBError(data.get("errors", ["Unknown error"]))

            return data
        except httpx.HTTPStatusError as e:
            raise SteamGridDBError(f"HTTP {e.response.status_code}: {e.response.text}")
        except httpx.RequestError as e:
            raise SteamGridDBError(f"Request failed: {e}")

    def search_game(self, name: str) -> list[SteamGridDBGame]:
        """Search for a game by name.

        Args:
            name: Game name to search for

        Returns:
            List of matching games
        """
        # URL encode the search term
        encoded_name = httpx.URL("", params={"q": name}).params["q"]
        data = self._request(f"/search/autocomplete/{encoded_name}")

        games = []
        for item in data.get("data", []):
            games.append(
                SteamGridDBGame(
                    id=item["id"],
                    name=item["name"],
                    release_date=item.get("release_date"),
                )
            )
        return games

    def _get_images(self, path: str) -> list[SteamGridDBImage]:
        """Get images from an endpoint."""
        data = self._request(path)

        images = []
        for item in data.get("data", []):
            images.append(
                SteamGridDBImage(
                    id=item["id"],
                    url=item["url"],
                    thumb=item.get("thumb", item["url"]),
                    width=item.get("width", 0),
                    height=item.get("height", 0),
                    style=item.get("style"),
                )
            )
        return images

    def get_grids(self, game_id: int) -> list[SteamGridDBImage]:
        """Get grid/poster images for a game.

        Args:
            game_id: SteamGridDB game ID

        Returns:
            List of available grid images
        """
        return self._get_images(f"/grids/game/{game_id}")

    def get_heroes(self, game_id: int) -> list[SteamGridDBImage]:
        """Get hero/banner images for a game.

        Args:
            game_id: SteamGridDB game ID

        Returns:
            List of available hero images
        """
        return self._get_images(f"/heroes/game/{game_id}")

    def get_logos(self, game_id: int) -> list[SteamGridDBImage]:
        """Get logo images for a game.

        Args:
            game_id: SteamGridDB game ID

        Returns:
            List of available logo images
        """
        return self._get_images(f"/logos/game/{game_id}")

    def get_icons(self, game_id: int) -> list[SteamGridDBImage]:
        """Get icon images for a game.

        Args:
            game_id: SteamGridDB game ID

        Returns:
            List of available icon images
        """
        return self._get_images(f"/icons/game/{game_id}")

    def download_image(self, url: str, dest: Path) -> bool:
        """Download an image to a destination path.

        Args:
            url: URL of the image to download
            dest: Destination file path

        Returns:
            True if download succeeded
        """
        try:
            # Use a separate client without auth headers for image downloads
            response = httpx.get(url, timeout=60.0, follow_redirects=True)
            response.raise_for_status()

            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(response.content)
            return True
        except Exception:
            return False

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "SteamGridDBClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
