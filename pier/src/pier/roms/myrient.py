"""Myrient ROM archive client for browsing and downloading ROMs."""

import html
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from pier.roms.systems import System


@dataclass
class MyrientFile:
    """A file from the Myrient archive."""

    name: str  # e.g., "Super Mario 64 (USA).zip"
    url: str  # Full download URL
    size: int  # File size in bytes
    date: str  # Last modified date string


class MyrientError(Exception):
    """Error from Myrient operations."""

    pass


class MyrientClient:
    """Client for browsing and downloading from Myrient ROM archives."""

    BASE_URL = "https://myrient.erista.me/files/"

    def __init__(self, timeout: float = 60.0):
        """Initialize the Myrient client.

        Args:
            timeout: HTTP request timeout in seconds.
        """
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "pier/1.0 (HTPC ROM Manager)",
            },
        )

    def _parse_index_html(self, html_content: str, base_url: str) -> list[MyrientFile]:
        """Parse Myrient's HTML index page to extract file listings.

        Args:
            html_content: Raw HTML content from the index page.
            base_url: Base URL for constructing full file URLs.

        Returns:
            List of MyrientFile objects.
        """
        files = []

        # Myrient uses a simple table format with links
        # Pattern: <a href="filename.zip">filename.zip</a> ... size ... date
        # The HTML structure is: <tr><td><a href="...">name</a></td><td>size</td><td>date</td></tr>

        # Match table rows containing file links (excluding parent directory)
        row_pattern = re.compile(
            r'<tr[^>]*>.*?<a\s+href="([^"]+)"[^>]*>([^<]+)</a>.*?'
            r"<td[^>]*>([^<]+)</td>.*?"
            r"<td[^>]*>([^<]+)</td>.*?</tr>",
            re.DOTALL | re.IGNORECASE,
        )

        for match in row_pattern.finditer(html_content):
            href, name, size_str, date_str = match.groups()

            # Skip parent directory link
            if href == "../" or name == "Parent Directory":
                continue

            # Skip if not a file (directories end with /)
            if href.endswith("/"):
                continue

            # Parse size (e.g., "15.6 MiB" -> bytes)
            size = self._parse_size(size_str.strip())

            # Clean up the filename
            name = html.unescape(name.strip())
            href = html.unescape(href.strip())

            # Construct full URL
            full_url = base_url.rstrip("/") + "/" + quote(href, safe="")

            files.append(
                MyrientFile(
                    name=name,
                    url=full_url,
                    size=size,
                    date=date_str.strip(),
                )
            )

        return files

    def _parse_size(self, size_str: str) -> int:
        """Parse a human-readable size string to bytes.

        Args:
            size_str: Size string like "15.6 MiB" or "256 KiB".

        Returns:
            Size in bytes.
        """
        size_str = size_str.strip()
        if not size_str or size_str == "-":
            return 0

        # Match number and optional unit
        match = re.match(r"([\d.]+)\s*(\w+)?", size_str)
        if not match:
            return 0

        value = float(match.group(1))
        unit = (match.group(2) or "B").upper()

        multipliers = {
            "B": 1,
            "KIB": 1024,
            "MIB": 1024 * 1024,
            "GIB": 1024 * 1024 * 1024,
            "KB": 1000,
            "MB": 1000 * 1000,
            "GB": 1000 * 1000 * 1000,
        }

        return int(value * multipliers.get(unit, 1))

    def list_files(self, system: System) -> list[MyrientFile]:
        """List all files available for a system.

        Args:
            system: The system to list files for.

        Returns:
            List of MyrientFile objects.

        Raises:
            MyrientError: If the request fails.
        """
        url = f"{self.BASE_URL}{system.myrient_path}/"

        try:
            response = self._client.get(url)
            response.raise_for_status()
            return self._parse_index_html(response.text, url)
        except httpx.HTTPStatusError as e:
            raise MyrientError(
                f"Failed to list files for {system.name}: HTTP {e.response.status_code}"
            ) from e
        except httpx.RequestError as e:
            raise MyrientError(f"Failed to connect to Myrient: {e}") from e

    def search(self, system: System, query: str) -> list[MyrientFile]:
        """Search for files matching a query within a system.

        Args:
            system: The system to search within.
            query: Search query (case-insensitive substring match).

        Returns:
            List of matching MyrientFile objects, sorted by relevance.
        """
        files = self.list_files(system)
        query_lower = query.lower()

        # Filter files that contain the query
        matches = [f for f in files if query_lower in f.name.lower()]

        # Sort by relevance: exact matches first, then by position of match
        def sort_key(f: MyrientFile) -> tuple[int, int, str]:
            name_lower = f.name.lower()
            # Exact match (minus extension) gets priority 0
            stem = Path(f.name).stem.lower()
            if stem == query_lower:
                return (0, 0, f.name)
            # Starts with query gets priority 1
            if name_lower.startswith(query_lower):
                return (1, 0, f.name)
            # Contains query - priority by position
            pos = name_lower.find(query_lower)
            return (2, pos, f.name)

        return sorted(matches, key=sort_key)

    def download(
        self,
        file: MyrientFile,
        dest_dir: Path,
        progress_callback: Callable[[int, int], None] | None = None,
        extract: bool = True,
    ) -> Path:
        """Download a file from Myrient.

        Args:
            file: The MyrientFile to download.
            dest_dir: Directory to save the file to.
            progress_callback: Optional callback(downloaded_bytes, total_bytes).
            extract: If True and file is a ZIP, extract contents.

        Returns:
            Path to the downloaded (or extracted) file.

        Raises:
            MyrientError: If the download fails.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Download to a temp file first
        zip_path = dest_dir / file.name

        try:
            with self._client.stream("GET", file.url) as response:
                response.raise_for_status()

                total = int(response.headers.get("content-length", 0))
                downloaded = 0

                with open(zip_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total)

        except httpx.HTTPStatusError as e:
            zip_path.unlink(missing_ok=True)
            raise MyrientError(
                f"Download failed: HTTP {e.response.status_code}"
            ) from e
        except httpx.RequestError as e:
            zip_path.unlink(missing_ok=True)
            raise MyrientError(f"Download failed: {e}") from e

        # Extract if it's a ZIP file
        if extract and file.name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    # Safely extract all files with path traversal protection
                    for member in zf.infolist():
                        member_path = Path(member.filename)
                        # Skip dangerous paths
                        if member_path.is_absolute() or ".." in member_path.parts:
                            continue
                        # Verify path stays within dest_dir
                        full_path = (dest_dir / member.filename).resolve()
                        try:
                            full_path.relative_to(dest_dir.resolve())
                        except ValueError:
                            continue  # Skip path traversal attempts
                        zf.extract(member, dest_dir)

                    # Find the main ROM file (largest file, excluding metadata)
                    rom_files = [
                        n
                        for n in zf.namelist()
                        if not n.endswith("/")
                        and not n.lower().endswith((".txt", ".nfo", ".xml", ".json"))
                        and not Path(n).is_absolute()
                        and ".." not in Path(n).parts
                    ]

                    if not rom_files:
                        zip_path.unlink(missing_ok=True)
                        raise MyrientError("ZIP archive contains no ROM files")

                    # Return path to the extracted ROM
                    largest = max(rom_files, key=lambda n: zf.getinfo(n).file_size)
                    extracted_path = dest_dir / largest

                    # Remove the ZIP file after successful extraction
                    zip_path.unlink()

                    return extracted_path

            except zipfile.BadZipFile as e:
                zip_path.unlink(missing_ok=True)
                raise MyrientError(f"Invalid ZIP file: {e}") from e

        return zip_path

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "MyrientClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
