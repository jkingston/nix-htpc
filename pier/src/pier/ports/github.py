"""GitHub Releases API client for downloading port releases."""

import fnmatch
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass
class GitHubAsset:
    """A release asset from GitHub."""

    name: str
    download_url: str
    size: int
    content_type: str


@dataclass
class GitHubRelease:
    """A GitHub release."""

    tag_name: str
    name: str
    published_at: str
    prerelease: bool
    assets: list[GitHubAsset]


class GitHubError(Exception):
    """Error from GitHub API operations."""

    pass


class GitHubClient:
    """Client for the GitHub Releases API."""

    BASE_URL = "https://api.github.com"

    def __init__(self, token: str | None = None, timeout: float = 30.0):
        """Initialize the GitHub client.

        Args:
            token: Optional GitHub personal access token for higher rate limits.
            timeout: HTTP request timeout in seconds.
        """
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pier/1.0 (HTPC ROM Manager)",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        self._client = httpx.Client(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )

    def _request(self, path: str) -> Any:
        """Make an API request.

        Args:
            path: API path (e.g., "/repos/owner/repo/releases/latest").

        Returns:
            JSON response data.

        Raises:
            GitHubError: If the request fails.
        """
        try:
            response = self._client.get(path)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise GitHubError(f"Not found: {path}") from e
            if e.response.status_code == 403:
                # Rate limit exceeded
                raise GitHubError(
                    "GitHub API rate limit exceeded. Set a GITHUB_TOKEN for higher limits."
                ) from e
            raise GitHubError(
                f"GitHub API error: HTTP {e.response.status_code}"
            ) from e
        except httpx.RequestError as e:
            raise GitHubError(f"Failed to connect to GitHub: {e}") from e

    def _parse_release(self, data: dict[str, Any]) -> GitHubRelease:
        """Parse release data from the API response."""
        assets = [
            GitHubAsset(
                name=a["name"],
                download_url=a["browser_download_url"],
                size=a["size"],
                content_type=a["content_type"],
            )
            for a in data.get("assets", [])
        ]

        return GitHubRelease(
            tag_name=data["tag_name"],
            name=data.get("name", data["tag_name"]),
            published_at=data["published_at"],
            prerelease=data.get("prerelease", False),
            assets=assets,
        )

    def get_latest_release(self, repo: str, include_prereleases: bool = False) -> GitHubRelease:
        """Get the latest release for a repository.

        Args:
            repo: Repository in "owner/repo" format.
            include_prereleases: If True, include pre-release versions.

        Returns:
            The latest GitHubRelease.

        Raises:
            GitHubError: If the request fails or no releases found.
        """
        if include_prereleases:
            # Get all releases and find the first one
            releases = self.get_releases(repo, limit=1)
            if not releases:
                raise GitHubError(f"No releases found for {repo}")
            return releases[0]

        data = self._request(f"/repos/{repo}/releases/latest")
        return self._parse_release(data)

    def get_releases(self, repo: str, limit: int = 10) -> list[GitHubRelease]:
        """Get recent releases for a repository.

        Args:
            repo: Repository in "owner/repo" format.
            limit: Maximum number of releases to return.

        Returns:
            List of GitHubRelease objects.
        """
        data = self._request(f"/repos/{repo}/releases?per_page={limit}")
        return [self._parse_release(r) for r in data]

    def find_asset(
        self,
        release: GitHubRelease,
        pattern: str,
    ) -> GitHubAsset | None:
        """Find an asset matching a glob pattern.

        Args:
            release: The release to search.
            pattern: Glob pattern to match (e.g., "*Linux*.zip").

        Returns:
            The matching GitHubAsset, or None if not found.
        """
        for asset in release.assets:
            if fnmatch.fnmatch(asset.name, pattern):
                return asset
        return None

    def download_asset(
        self,
        asset: GitHubAsset,
        dest_dir: Path,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Download a release asset.

        Args:
            asset: The asset to download.
            dest_dir: Directory to save the file to.
            progress_callback: Optional callback(downloaded_bytes, total_bytes).

        Returns:
            Path to the downloaded file.

        Raises:
            GitHubError: If the download fails.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / asset.name

        try:
            # Use a separate client for downloads (different headers)
            with httpx.Client(
                timeout=300.0,  # 5 minute timeout for large files
                follow_redirects=True,
            ) as download_client:
                with download_client.stream("GET", asset.download_url) as response:
                    response.raise_for_status()

                    total = int(response.headers.get("content-length", asset.size))
                    downloaded = 0

                    with open(dest_path, "wb") as f:
                        for chunk in response.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback:
                                progress_callback(downloaded, total)

        except httpx.HTTPStatusError as e:
            dest_path.unlink(missing_ok=True)
            raise GitHubError(
                f"Download failed: HTTP {e.response.status_code}"
            ) from e
        except httpx.RequestError as e:
            dest_path.unlink(missing_ok=True)
            raise GitHubError(f"Download failed: {e}") from e

        return dest_path

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
