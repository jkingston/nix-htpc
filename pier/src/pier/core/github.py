"""GitHub releases API for fetching ports."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pier.core.constants import DOWNLOAD_CHUNK_SIZE, RELEASE_FETCH_LIMIT
from pier.core.http import GitHubAPIClient


@dataclass
class ReleaseAsset:
    """A release asset from GitHub."""

    name: str
    download_url: str
    size: int  # bytes
    content_type: str


@dataclass
class Release:
    """A GitHub release."""

    tag_name: str
    name: str
    prerelease: bool
    published_at: str
    assets: list[ReleaseAsset]

    def find_asset(self, pattern: str) -> ReleaseAsset | None:
        """Find an asset matching a pattern (case-insensitive)."""
        pattern_lower = pattern.lower()
        for asset in self.assets:
            if pattern_lower in asset.name.lower():
                return asset
        return None


class GitHubClient(GitHubAPIClient):
    """Client for GitHub releases API."""

    def __init__(self, token: str | None = None) -> None:
        """Initialize the client.

        Args:
            token: Optional GitHub token for higher rate limits
        """
        super().__init__(token=token)

    async def get_latest_release(self, repo: str) -> Release:
        """Get the latest release for a repository.

        Args:
            repo: Repository in "owner/repo" format

        Returns:
            Latest release information
        """
        client = await self._get_client()
        url = f"{self.api_base}/repos/{repo}/releases/latest"
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

        return Release(
            tag_name=data["tag_name"],
            name=data.get("name", data["tag_name"]),
            prerelease=data.get("prerelease", False),
            published_at=data.get("published_at", ""),
            assets=[
                ReleaseAsset(
                    name=asset["name"],
                    download_url=asset["browser_download_url"],
                    size=asset["size"],
                    content_type=asset.get("content_type", ""),
                )
                for asset in data.get("assets", [])
            ],
        )

    async def get_releases(self, repo: str, limit: int = RELEASE_FETCH_LIMIT) -> list[Release]:
        """Get recent releases for a repository.

        Args:
            repo: Repository in "owner/repo" format
            limit: Maximum releases to return

        Returns:
            List of releases
        """
        client = await self._get_client()
        url = f"{self.api_base}/repos/{repo}/releases"
        response = await client.get(url, params={"per_page": limit})
        response.raise_for_status()
        releases_data = response.json()

        return [
            Release(
                tag_name=data["tag_name"],
                name=data.get("name", data["tag_name"]),
                prerelease=data.get("prerelease", False),
                published_at=data.get("published_at", ""),
                assets=[
                    ReleaseAsset(
                        name=asset["name"],
                        download_url=asset["browser_download_url"],
                        size=asset["size"],
                        content_type=asset.get("content_type", ""),
                    )
                    for asset in data.get("assets", [])
                ],
            )
            for data in releases_data
        ]

    async def download_asset(
        self,
        asset: ReleaseAsset,
        dest_dir: Path,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Download a release asset.

        Args:
            asset: Asset to download
            dest_dir: Directory to save the file
            progress_callback: Optional callback(downloaded, total)

        Returns:
            Path to downloaded file
        """
        client = await self._get_client()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / asset.name

        async with client.stream("GET", asset.download_url) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", asset.size))
            downloaded = 0

            with open(dest_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)

        return dest_path


# Synchronous wrappers for CLI use
def get_latest_release_sync(repo: str) -> Release:
    """Synchronous wrapper for getting latest release."""

    async def _get():
        client = GitHubClient()
        try:
            return await client.get_latest_release(repo)
        finally:
            await client.close()

    return asyncio.run(_get())


def download_asset_sync(
    asset: ReleaseAsset,
    dest_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path:
    """Synchronous wrapper for downloading an asset."""

    async def _download():
        client = GitHubClient()
        try:
            return await client.download_asset(asset, dest_dir, progress_callback)
        finally:
            await client.close()

    return asyncio.run(_download())
