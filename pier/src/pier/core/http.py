"""Base HTTP client for pier."""

from typing import Any

import httpx

from pier.core.constants import HTTP_TIMEOUT, USER_AGENT


class AsyncHTTPClient:
    """Base class for async HTTP clients with connection management."""

    def __init__(
        self,
        timeout: float = HTTP_TIMEOUT,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ):
        """Initialize the client.

        Args:
            timeout: Request timeout in seconds
            headers: Additional headers to include in requests
            follow_redirects: Whether to follow redirects
        """
        self._timeout = timeout
        self._headers = headers or {}
        self._follow_redirects = follow_redirects
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers=self._headers,
                follow_redirects=self._follow_redirects,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "AsyncHTTPClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()


class GitHubAPIClient(AsyncHTTPClient):
    """Base class for GitHub API clients."""

    def __init__(self, token: str | None = None, api_base: str | None = None):
        """Initialize the GitHub client.

        Args:
            token: Optional GitHub token for higher rate limits
            api_base: API base URL (defaults to api.github.com)
        """
        from pier.core.constants import GITHUB_API_BASE

        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"token {token}"

        super().__init__(timeout=HTTP_TIMEOUT, headers=headers, follow_redirects=True)
        self.api_base = api_base or GITHUB_API_BASE
        self.token = token


class SteamGridDBClient(AsyncHTTPClient):
    """Base class for SteamGridDB API clients."""

    def __init__(self, api_key: str):
        """Initialize with API key.

        Args:
            api_key: SteamGridDB API key
        """
        from pier.core.constants import STEAMGRIDDB_API_BASE

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

        super().__init__(timeout=HTTP_TIMEOUT, headers=headers)
        self.api_base = STEAMGRIDDB_API_BASE
        self.api_key = api_key
