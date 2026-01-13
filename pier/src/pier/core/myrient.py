"""Myrient ROM browser and downloader."""

import asyncio
import hashlib
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from pier.core.constants import (
    DOWNLOAD_CHUNK_SIZE,
    DOWNLOAD_TIMEOUT,
    MYRIENT_BASE_URL,
    SEARCH_RESULT_LIMIT,
    USER_AGENT,
)
from pier.core.errors import UnknownSystemError
from pier.core.http import AsyncHTTPClient
from pier.core.registry import SYSTEMS


@dataclass
class MyrientEntry:
    """An entry in a myrient directory listing."""

    name: str
    path: str  # URL-encoded path
    size: str  # Human-readable size
    is_directory: bool
    modified: str | None = None


class MyrientBrowser(AsyncHTTPClient):
    """Browse and download ROMs from myrient.erista.me."""

    def __init__(self) -> None:
        super().__init__(
            timeout=DOWNLOAD_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        self.base_url = MYRIENT_BASE_URL

    async def list_directory(self, path: str) -> list[MyrientEntry]:
        """List contents of a myrient directory.

        Args:
            path: URL-encoded path relative to BASE_URL

        Returns:
            List of entries in the directory
        """
        client = await self._get_client()
        url = f"{self.base_url}/{path}/"
        response = await client.get(url)
        response.raise_for_status()

        entries = []
        html = response.text

        # Parse the HTML directory listing
        # Myrient uses a standard Apache-style listing
        # Pattern matches: <a href="filename">filename</a> ... <td>size</td> <td>date</td>
        pattern = r'<a href="([^"]+)"[^>]*>([^<]+)</a>\s*</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>'

        for match in re.finditer(pattern, html):
            href, name, size, modified = match.groups()

            # Skip parent directory link
            if href == "../" or name == "Parent Directory":
                continue

            # Check if it's a directory (ends with /)
            is_dir = href.endswith("/")

            # Clean up the href
            entry_path = f"{path}/{href.rstrip('/')}" if path else href.rstrip("/")

            entries.append(
                MyrientEntry(
                    name=unquote(name.strip()),
                    path=entry_path,
                    size=size.strip() if size.strip() != "-" else "",
                    is_directory=is_dir,
                    modified=modified.strip() if modified.strip() else None,
                )
            )

        return entries

    async def list_system(self, system_id: str) -> list[MyrientEntry]:
        """List ROMs for a specific system.

        Args:
            system_id: System ID (e.g., "n64", "snes")

        Returns:
            List of ROM entries
        """
        system = SYSTEMS.get(system_id)
        if not system:
            raise UnknownSystemError(system_id)

        return await self.list_directory(system.myrient_path)

    async def search(
        self, system_id: str, query: str, limit: int = SEARCH_RESULT_LIMIT
    ) -> list[MyrientEntry]:
        """Search for ROMs matching a query.

        Args:
            system_id: System ID to search in
            query: Search query (case-insensitive substring match)
            limit: Maximum results to return

        Returns:
            Matching ROM entries
        """
        entries = await self.list_system(system_id)
        query_lower = query.lower()

        matches = [
            entry
            for entry in entries
            if query_lower in entry.name.lower() and not entry.is_directory
        ]

        return matches[:limit]

    async def download(
        self,
        path: str,
        dest_dir: Path,
        progress_callback: Callable[[int, int], None] | None = None,
        extract: bool = True,
    ) -> Path:
        """Download a file from myrient.

        Args:
            path: URL-encoded path to file
            dest_dir: Directory to save the file
            progress_callback: Optional callback(downloaded, total) for progress
            extract: If True and file is a zip, extract and return extracted file

        Returns:
            Path to downloaded (or extracted) file
        """
        client = await self._get_client()
        url = f"{self.base_url}/{path}"

        # Get filename from path
        filename = unquote(path.split("/")[-1])
        dest_path = dest_dir / filename

        dest_dir.mkdir(parents=True, exist_ok=True)

        # Download with streaming
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            downloaded = 0

            with open(dest_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)

        # Extract if it's a zip file
        if extract and dest_path.suffix.lower() == ".zip":
            extracted_path = self._extract_zip(dest_path, dest_dir)
            dest_path.unlink()  # Remove the zip after extraction
            return extracted_path

        return dest_path

    def _extract_zip(self, zip_path: Path, dest_dir: Path) -> Path:
        """Extract a zip file and return the main ROM file."""
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Find the ROM file (usually the largest file)
            rom_extensions = {
                ".z64",
                ".n64",
                ".v64",
                ".sfc",
                ".smc",
                ".nes",
                ".gba",
                ".md",
                ".bin",
                ".iso",
                ".cue",
                ".rvz",
                ".gcm",
                ".wbfs",
            }

            rom_file = None
            for name in zf.namelist():
                if any(name.lower().endswith(ext) for ext in rom_extensions):
                    rom_file = name
                    break

            if not rom_file:
                # Just extract all and return the first file
                zf.extractall(dest_dir)
                for name in zf.namelist():
                    return dest_dir / name
                raise ValueError("Downloaded ZIP file is empty")
            else:
                # Extract just the ROM file
                zf.extract(rom_file, dest_dir)
                return dest_dir / rom_file

    async def download_for_port(
        self,
        system_id: str,
        myrient_filename: str,
        dest_dir: Path,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Download a ROM for a port.

        Args:
            system_id: System ID (e.g., "n64")
            myrient_filename: URL-encoded filename on myrient
            dest_dir: Directory to save the ROM
            progress_callback: Optional progress callback

        Returns:
            Path to the downloaded ROM
        """
        system = SYSTEMS.get(system_id)
        if not system:
            raise UnknownSystemError(system_id)

        path = f"{system.myrient_path}/{myrient_filename}"
        return await self.download(path, dest_dir, progress_callback)


def verify_hash(file_path: Path, hash_type: str, expected_hash: str) -> bool:
    """Verify a file's hash.

    Args:
        file_path: Path to the file
        hash_type: "sha1" or "md5"
        expected_hash: Expected hash value (case-insensitive)

    Returns:
        True if hash matches
    """
    if not expected_hash:
        return True  # No hash to verify

    if hash_type == "sha1":
        hasher = hashlib.sha1()
    elif hash_type == "md5":
        hasher = hashlib.md5()
    else:
        raise ValueError(f"Unknown hash type: {hash_type}")

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)

    actual_hash = hasher.hexdigest()
    return actual_hash.lower() == expected_hash.lower()


# Synchronous wrapper for CLI use
def download_rom_sync(
    system_id: str,
    myrient_filename: str,
    dest_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path:
    """Synchronous wrapper for downloading a ROM."""

    async def _download():
        browser = MyrientBrowser()
        try:
            return await browser.download_for_port(
                system_id, myrient_filename, dest_dir, progress_callback
            )
        finally:
            await browser.close()

    return asyncio.run(_download())
