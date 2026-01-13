"""BIOS file management for pier."""

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import httpx

from pier.core.config import Config
from pier.core.constants import (
    DOWNLOAD_CHUNK_SIZE,
    DOWNLOAD_TIMEOUT,
    HASH_CHUNK_SIZE,
    RETROARCH_SYSTEM_RAW,
)
from pier.core.errors import BIOSHashMismatchError, UnknownBIOSError
from pier.core.http import AsyncHTTPClient


class BiosStatus(Enum):
    """Status of a BIOS file."""

    VALID = "valid"  # Present and hash matches
    INVALID = "invalid"  # Present but hash mismatch
    MISSING = "missing"  # Not present


@dataclass
class BiosFile:
    """A BIOS file definition."""

    filename: str
    system: str  # ps1, ps2, gba, gb, gbc
    md5: str
    description: str
    priority: int  # 1 = recommended, 2 = alternative
    github_path: str  # Path in retroarch_system repo


@dataclass
class BiosCheckResult:
    """Result of checking a BIOS file."""

    bios: BiosFile
    status: BiosStatus
    actual_md5: str | None = None


# BIOS file registry - recommended files for each system
BIOS_REGISTRY: list[BiosFile] = [
    # PlayStation 1
    BiosFile(
        filename="scph5501.bin",
        system="ps1",
        md5="490f666e1afb15b7362b406ed1cea246",
        description="PS1 USA BIOS (recommended for DuckStation)",
        priority=1,
        github_path="Sony - PlayStation/scph5501.bin",
    ),
    BiosFile(
        filename="PSXONPSP660.bin",
        system="ps1",
        md5="c53ca5908936d412331790f4426c6c33",
        description="PS1 Region-free BIOS (from PSP, good for RetroArch)",
        priority=2,
        github_path="Sony - PlayStation/PSXONPSP660.bin",
    ),
    BiosFile(
        filename="scph5502.bin",
        system="ps1",
        md5="32736f17079d0b2b7024407c39bd3050",
        description="PS1 PAL BIOS",
        priority=2,
        github_path="Sony - PlayStation/scph5502.bin",
    ),
    BiosFile(
        filename="scph5500.bin",
        system="ps1",
        md5="8dd7d5296a650fac7319bce665a6a53c",
        description="PS1 Japan BIOS",
        priority=2,
        github_path="Sony - PlayStation/scph5500.bin",
    ),
    # PlayStation 2
    BiosFile(
        filename="scph39001.bin",
        system="ps2",
        md5="d5ce2c7d119f563ce04bc04dbc3a323e",
        description="PS2 USA BIOS v1.60",
        priority=1,
        github_path="Sony - PlayStation 2/scph39001.bin",
    ),
    BiosFile(
        filename="SCPH-70012_BIOS_V12_USA_200.BIN",
        system="ps2",
        md5="d333558cc14561c1fdc334c75d5f37b7",
        description="PS2 USA BIOS v2.00 (slim, recommended)",
        priority=1,
        github_path="Sony - PlayStation 2/SCPH-70012_BIOS_V12_USA_200.BIN",
    ),
    # Game Boy Advance
    BiosFile(
        filename="gba_bios.bin",
        system="gba",
        md5="a860e8c0b6d573d191e4ec7db1b1e4f6",
        description="GBA BIOS (optional - mGBA has HLE fallback)",
        priority=2,
        github_path="Nintendo - Game Boy Advance/gba_bios.bin",
    ),
    # Game Boy
    BiosFile(
        filename="gb_bios.bin",
        system="gb",
        md5="32fbbd84168d3482956eb3c5051637f5",
        description="Game Boy BIOS (optional - for boot logo)",
        priority=2,
        github_path="Nintendo - Game Boy/gb_bios.bin",
    ),
    # Game Boy Color
    BiosFile(
        filename="gbc_bios.bin",
        system="gbc",
        md5="dbfce9db9deaa2567f6a84fde55f9680",
        description="Game Boy Color BIOS (optional - for boot logo)",
        priority=2,
        github_path="Nintendo - Game Boy Color/gbc_bios.bin",
    ),
]


def get_bios_by_filename(filename: str) -> BiosFile | None:
    """Get a BIOS file by filename."""
    for bios in BIOS_REGISTRY:
        if bios.filename.lower() == filename.lower():
            return bios
    return None


def get_bios_by_system(system: str) -> list[BiosFile]:
    """Get all BIOS files for a system."""
    return [bios for bios in BIOS_REGISTRY if bios.system == system]


def get_recommended_bios() -> list[BiosFile]:
    """Get all recommended (priority 1) BIOS files."""
    return [bios for bios in BIOS_REGISTRY if bios.priority == 1]


def verify_md5(file_path: Path, expected_md5: str) -> bool:
    """Verify a file's MD5 hash.

    Args:
        file_path: Path to the file
        expected_md5: Expected MD5 hash (case-insensitive)

    Returns:
        True if hash matches
    """
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest().lower() == expected_md5.lower()


def compute_md5(file_path: Path) -> str:
    """Compute MD5 hash of a file."""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class BiosManager(AsyncHTTPClient):
    """Manages BIOS file verification and downloading."""

    def __init__(self, config: Config | None = None):
        """Initialize the BIOS manager.

        Args:
            config: Optional config, loads default if not provided
        """
        super().__init__(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True)
        self.config = config or Config.load()
        self.bios_dir = self.config.emulation_dir / "bios"
        self.base_url = RETROARCH_SYSTEM_RAW

    def _get_bios_path(self, bios: BiosFile) -> Path:
        """Get the path where a BIOS file should be stored."""
        return self.bios_dir / bios.filename

    @staticmethod
    def _make_file_callback(
        filename: str,
        progress_callback: Callable[[str, int, int], None] | None,
    ) -> Callable[[int, int], None] | None:
        """Create a per-file progress callback wrapper.

        Args:
            filename: Name of the file being downloaded
            progress_callback: Outer callback that receives (filename, downloaded, total)

        Returns:
            Wrapped callback that receives (downloaded, total), or None
        """
        if not progress_callback:
            return None

        def callback(downloaded: int, total: int):
            progress_callback(filename, downloaded, total)

        return callback

    def check_file(self, bios: BiosFile) -> BiosCheckResult:
        """Check the status of a single BIOS file.

        Args:
            bios: BIOS file to check

        Returns:
            BiosCheckResult with status and actual hash if present
        """
        path = self._get_bios_path(bios)

        if not path.exists():
            return BiosCheckResult(bios=bios, status=BiosStatus.MISSING)

        actual_md5 = compute_md5(path)
        if actual_md5.lower() == bios.md5.lower():
            return BiosCheckResult(bios=bios, status=BiosStatus.VALID, actual_md5=actual_md5)
        else:
            return BiosCheckResult(bios=bios, status=BiosStatus.INVALID, actual_md5=actual_md5)

    def check_all(self) -> list[BiosCheckResult]:
        """Check all BIOS files in the registry.

        Returns:
            List of BiosCheckResult for each file
        """
        return [self.check_file(bios) for bios in BIOS_REGISTRY]

    def check_recommended(self) -> list[BiosCheckResult]:
        """Check only recommended (priority 1) BIOS files.

        Returns:
            List of BiosCheckResult for recommended files
        """
        return [self.check_file(bios) for bios in get_recommended_bios()]

    async def download(
        self,
        bios: BiosFile,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Download a BIOS file from retroarch_system repo.

        Args:
            bios: BIOS file to download
            progress_callback: Optional callback(downloaded, total) for progress

        Returns:
            Path to downloaded file

        Raises:
            httpx.HTTPStatusError: If download fails
            ValueError: If downloaded file hash doesn't match
        """
        client = await self._get_client()
        url = f"{self.base_url}/{bios.github_path}"

        self.bios_dir.mkdir(parents=True, exist_ok=True)
        dest_path = self._get_bios_path(bios)

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

        # Verify hash
        actual_md5 = compute_md5(dest_path)
        if actual_md5.lower() != bios.md5.lower():
            dest_path.unlink()
            raise BIOSHashMismatchError(bios.filename, bios.md5, actual_md5)

        return dest_path

    async def download_by_filename(
        self,
        filename: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Download a BIOS file by filename.

        Args:
            filename: BIOS filename to download
            progress_callback: Optional progress callback

        Returns:
            Path to downloaded file

        Raises:
            UnknownBIOSError: If filename not in registry
        """
        bios = get_bios_by_filename(filename)
        if not bios:
            raise UnknownBIOSError(filename)
        return await self.download(bios, progress_callback)

    async def download_recommended(
        self,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> list[Path]:
        """Download all recommended (priority 1) BIOS files.

        Args:
            progress_callback: Optional callback(filename, downloaded, total)

        Returns:
            List of paths to downloaded files
        """
        paths = []
        for bios in get_recommended_bios():
            # Skip if already valid
            result = self.check_file(bios)
            if result.status == BiosStatus.VALID:
                continue

            file_callback = self._make_file_callback(bios.filename, progress_callback)
            try:
                path = await self.download(bios, file_callback)
                paths.append(path)
            except (httpx.HTTPStatusError, BIOSHashMismatchError):
                # Continue with other files if one fails
                pass

        return paths

    async def download_all(
        self,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> list[Path]:
        """Download all BIOS files in the registry.

        Args:
            progress_callback: Optional callback(filename, downloaded, total)

        Returns:
            List of paths to downloaded files
        """
        paths = []
        for bios in BIOS_REGISTRY:
            # Skip if already valid
            result = self.check_file(bios)
            if result.status == BiosStatus.VALID:
                continue

            file_callback = self._make_file_callback(bios.filename, progress_callback)
            try:
                path = await self.download(bios, file_callback)
                paths.append(path)
            except (httpx.HTTPStatusError, BIOSHashMismatchError):
                pass

        return paths


# Synchronous wrappers for CLI use
def check_bios_sync() -> list[BiosCheckResult]:
    """Synchronous wrapper for checking all BIOS files."""
    manager = BiosManager()
    return manager.check_all()


def download_bios_sync(
    filename: str,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path:
    """Synchronous wrapper for downloading a BIOS file."""

    async def _download():
        manager = BiosManager()
        try:
            return await manager.download_by_filename(filename, progress_callback)
        finally:
            await manager.close()

    return asyncio.run(_download())


def download_recommended_sync(
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> list[Path]:
    """Synchronous wrapper for downloading recommended BIOS files."""

    async def _download():
        manager = BiosManager()
        try:
            return await manager.download_recommended(progress_callback)
        finally:
            await manager.close()

    return asyncio.run(_download())


def download_all_sync(
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> list[Path]:
    """Synchronous wrapper for downloading all BIOS files."""

    async def _download():
        manager = BiosManager()
        try:
            return await manager.download_all(progress_callback)
        finally:
            await manager.close()

    return asyncio.run(_download())
