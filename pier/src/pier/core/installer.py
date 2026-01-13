"""Port installation logic."""

import asyncio
import shutil
import stat
import subprocess
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path

from pier.core.artwork import ArtworkManager
from pier.core.config import Config, Library
from pier.core.constants import PIER_TAG, PORTS_TAG, STEAM_RUN_EXECUTABLE
from pier.core.errors import (
    AssetGenerationError,
    ExecutableNotFoundError,
    InstallError,
    ROMHashMismatchError,
    UnknownPortError,
    UnknownSystemError,
    UnsafeArchiveError,
)
from pier.core.github import GitHubClient
from pier.core.myrient import MyrientBrowser, verify_hash
from pier.core.registry import (
    SYSTEMS,
    AssetGenerator,
    Port,
    get_port,
)
from pier.core.steam import SteamLibrary


class ProgressReporter:
    """Reports installation progress."""

    def __init__(
        self,
        on_status: Callable[[str], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ):
        self.on_status = on_status or (lambda _s: None)
        self.on_progress = on_progress or (lambda _d, _t: None)

    def status(self, message: str):
        """Report a status message."""
        self.on_status(message)

    def progress(self, downloaded: int, total: int):
        """Report download progress."""
        self.on_progress(downloaded, total)


class PortInstaller:
    """Handles full port installation flow."""

    def __init__(
        self,
        config: Config | None = None,
        library: Library | None = None,
        progress: ProgressReporter | None = None,
    ):
        self.config = config or Config.load()
        self.library = library or Library.load(self.config.pier_dir)
        self.progress = progress or ProgressReporter()

    async def install(
        self,
        port_id: str,
        with_mods: bool = True,
        add_to_steam: bool = True,
        fetch_artwork: bool = True,
    ) -> Path:
        """Install a native game port.

        Args:
            port_id: Port ID to install
            with_mods: Install HD texture packs if available
            add_to_steam: Add shortcut to Steam
            fetch_artwork: Download artwork from SteamGridDB

        Returns:
            Path to installed port directory

        Raises:
            InstallError: If installation fails
        """
        port = get_port(port_id)
        if not port:
            raise UnknownPortError(port_id)

        port_dir = self.config.ports_dir / port_id
        port_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Ensure ROM is available
            rom_path = await self._ensure_rom(port)

            # 2. Download port from GitHub
            executable_path = await self._download_port(port, port_dir)

            # 3. Generate game assets
            await self._generate_assets(port, port_dir, rom_path)

            # 4. Install mods if requested
            if with_mods and port.mods:
                await self._install_mods(port, port_dir)

            # 5. Add to Steam if requested
            if add_to_steam:
                artwork_data = None
                if fetch_artwork:
                    artwork_data = await self._fetch_artwork(port)
                await self._add_to_steam(port, executable_path, artwork_data)

            # 6. Record in library
            version = await self._get_installed_version(port)
            self.library.add_port(
                port_id,
                version=version,
                executable=str(executable_path),
            )
            self.library.set_steam_link(port_id, add_to_steam)
            self.library.save(self.config.pier_dir)

            self.progress.status(f"Installation complete: {port.name}")
            return port_dir

        except Exception as e:
            raise InstallError(f"Failed to install {port.name}: {e}") from e

    async def _ensure_rom(self, port: Port) -> Path:
        """Ensure the required ROM is available."""
        rom = port.rom
        system = SYSTEMS.get(rom.system)
        if not system:
            raise UnknownSystemError(rom.system)

        rom_dir = self.config.roms_dir / rom.system
        rom_path = rom_dir / rom.filename

        if rom_path.exists():
            self.progress.status(f"ROM found: {rom.filename}")
            # Verify hash if specified
            if rom.hash_value and not verify_hash(rom_path, rom.hash_type, rom.hash_value):
                raise ROMHashMismatchError(rom.filename, rom.hash_value, "verification failed")
            return rom_path

        # Download from myrient
        self.progress.status(f"Downloading ROM: {rom.name}")
        browser = MyrientBrowser()
        try:
            rom_path = await browser.download_for_port(
                rom.system,
                rom.myrient_path,
                rom_dir,
                self.progress.progress,
            )
        finally:
            await browser.close()

        # Verify hash
        if rom.hash_value and not verify_hash(rom_path, rom.hash_type, rom.hash_value):
            rom_path.unlink()
            raise ROMHashMismatchError(rom.filename, rom.hash_value, "download verification failed")

        self.progress.status(f"ROM ready: {rom_path.name}")
        self.library.add_rom(rom.system, rom_path.name)
        return rom_path

    async def _download_port(self, port: Port, port_dir: Path) -> Path:
        """Download the port from GitHub releases."""
        self.progress.status(f"Fetching {port.name} release info...")

        github = GitHubClient()
        try:
            release = await github.get_latest_release(port.repo)
            asset = release.find_asset(port.asset_pattern)
            if not asset:
                raise InstallError(f"No Linux asset found in release {release.tag_name}")

            self.progress.status(f"Downloading {port.name} {release.tag_name}...")

            download_path = await github.download_asset(asset, port_dir, self.progress.progress)
        finally:
            await github.close()

        # Extract if archive
        executable_path = self._extract_and_find_executable(download_path, port_dir, port)

        # Save version
        (port_dir / ".version").write_text(release.tag_name)

        return executable_path

    def _extract_and_find_executable(self, download_path: Path, port_dir: Path, port: Port) -> Path:
        """Extract downloaded archive and find the executable."""
        suffix = download_path.suffix.lower()

        if suffix == ".appimage":
            # AppImage is already executable
            executable = port_dir / port.executable
            if download_path != executable:
                shutil.move(str(download_path), str(executable))
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
            return executable

        elif suffix == ".zip":
            with zipfile.ZipFile(download_path, "r") as zf:
                # Security: validate all paths before extraction to prevent path traversal
                for name in zf.namelist():
                    member_path = (port_dir / name).resolve()
                    if not member_path.is_relative_to(port_dir.resolve()):
                        raise UnsafeArchiveError(download_path.name, name)
                zf.extractall(port_dir)
            download_path.unlink()

        elif suffix in (".tar", ".gz", ".tgz", ".xz"):
            with tarfile.open(download_path, "r:*") as tf:
                # Security: validate all paths before extraction to prevent path traversal
                for member in tf.getmembers():
                    member_path = (port_dir / member.name).resolve()
                    if not member_path.is_relative_to(port_dir.resolve()):
                        raise UnsafeArchiveError(download_path.name, member.name)
                tf.extractall(port_dir)
            download_path.unlink()

        # Find executable
        executable = port_dir / port.executable
        if not executable.exists():
            # Search in subdirectories
            for f in port_dir.rglob(port.executable):
                executable = f
                break

        if not executable.exists():
            raise ExecutableNotFoundError(port.executable, str(port_dir))

        # Make executable
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        return executable

    async def _generate_assets(self, port: Port, port_dir: Path, rom_path: Path):
        """Generate game assets from ROM."""
        if port.asset_generator == AssetGenerator.NONE:
            return

        elif port.asset_generator == AssetGenerator.COPY:
            # Just copy ROM to expected location
            if port.rom.copy_as:
                dest = port_dir / port.rom.copy_as
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(rom_path, dest)
                self.progress.status(f"ROM copied to {port.rom.copy_as}")
            return

        elif port.asset_generator == AssetGenerator.TORCH:
            await self._run_torch(port, port_dir, rom_path)

        elif port.asset_generator == AssetGenerator.OPENGOAL:
            await self._run_opengoal_extractor(port, port_dir, rom_path)

    async def _run_torch(self, port: Port, port_dir: Path, rom_path: Path):
        """Run HarbourMasters torch tool to generate .o2r/.otr files."""
        self.progress.status("Generating game assets with torch...")

        # Find or extract torch
        torch_path = port_dir / "torch"
        if not torch_path.exists():
            # Extract from AppImage if needed
            appimage = port_dir / port.executable
            if port.extract_torch and appimage.exists():
                # AppImages can be extracted with --appimage-extract
                self.progress.status("Extracting torch from AppImage...")
                result = subprocess.run(
                    ["steam-run", str(appimage), "--appimage-extract", "usr/bin/torch"],
                    cwd=port_dir,
                    capture_output=True,
                )
                extracted_torch = port_dir / "squashfs-root" / "usr" / "bin" / "torch"
                if extracted_torch.exists():
                    shutil.move(str(extracted_torch), str(torch_path))
                    shutil.rmtree(port_dir / "squashfs-root", ignore_errors=True)
                    torch_path.chmod(torch_path.stat().st_mode | stat.S_IXUSR)

        if not torch_path.exists():
            raise InstallError("Could not find or extract torch tool")

        # Run torch to generate assets
        # Format: torch otr <rom_path>
        self.progress.status("Running torch...")
        result = subprocess.run(
            ["steam-run", str(torch_path), "otr", str(rom_path)],
            cwd=port_dir,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            error_output = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            raise AssetGenerationError("torch", result.returncode, error_output)

        self.progress.status("Game assets generated")

    async def _run_opengoal_extractor(self, port: Port, port_dir: Path, rom_path: Path):
        """Run OpenGOAL extractor to extract game assets from ISO."""
        self.progress.status("Extracting game assets with OpenGOAL...")

        extractor = port_dir / "extractor"
        if not extractor.exists():
            # Look in common locations
            for name in ["extractor", "extractor.exe"]:
                for f in port_dir.rglob(name):
                    extractor = f
                    break

        if not extractor.exists():
            raise InstallError("OpenGOAL extractor not found")

        # Determine game from port ID
        game = port.id.replace("opengoal-", "")  # jak1, jak2, jak3

        result = subprocess.run(
            ["steam-run", str(extractor), "--game", game, str(rom_path)],
            cwd=port_dir,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            error_output = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            raise AssetGenerationError("OpenGOAL extractor", result.returncode, error_output)

        self.progress.status("Game assets extracted")

    async def _install_mods(self, port: Port, port_dir: Path):
        """Install HD texture packs and mods."""
        mods_dir = port_dir / "mods"
        mods_dir.mkdir(exist_ok=True)

        github = GitHubClient()
        try:
            for mod in port.mods:
                self.progress.status(f"Installing mod: {mod.name}")

                release = await github.get_latest_release(mod.repo)
                asset = release.find_asset(mod.asset_pattern)
                if not asset:
                    self.progress.status(f"  No asset found for {mod.name}")
                    continue

                await github.download_asset(asset, mods_dir, self.progress.progress)
                self.progress.status(f"  Installed: {mod.name}")
        finally:
            await github.close()

    async def _fetch_artwork(self, port: Port) -> dict | None:
        """Fetch artwork for the port."""
        self.progress.status("Fetching artwork...")

        manager = ArtworkManager(self.config.steamgriddb_api_key)
        try:
            artwork = await manager.fetch_for_port(port.game)
            if artwork.grid:
                self.progress.status("Artwork downloaded")
                return {"grid": artwork.grid, "hero": artwork.hero, "logo": artwork.logo}
        finally:
            await manager.close()

        return None

    async def _add_to_steam(self, port: Port, executable: Path, artwork: dict | None):
        """Add port to Steam library."""
        self.progress.status("Adding to Steam...")

        try:
            steam = SteamLibrary()
            shortcut = steam.add_shortcut(
                app_name=port.name,
                exe=STEAM_RUN_EXECUTABLE,
                start_dir=str(executable.parent),
                launch_options=f'"{executable}"',
                tags=[PIER_TAG, PORTS_TAG, port.game],
            )

            # Install artwork if available
            if artwork:
                grid_id = shortcut.grid_id
                if artwork.get("grid"):
                    (steam.grid_path / f"{grid_id}p.png").write_bytes(artwork["grid"])
                if artwork.get("hero"):
                    (steam.grid_path / f"{grid_id}_hero.png").write_bytes(artwork["hero"])
                if artwork.get("logo"):
                    (steam.grid_path / f"{grid_id}_logo.png").write_bytes(artwork["logo"])

            self.progress.status("Steam shortcut created")
        except FileNotFoundError:
            self.progress.status("Steam not found, skipping shortcut")

    async def _get_installed_version(self, port: Port) -> str:
        """Get the installed version of a port."""
        version_file = self.config.ports_dir / port.id / ".version"
        if version_file.exists():
            return version_file.read_text().strip()
        return "unknown"

    async def check_update(self, port_id: str) -> tuple[str, str] | None:
        """Check if an update is available for a port.

        Returns:
            Tuple of (current_version, new_version) if update available, else None
        """
        port = get_port(port_id)
        if not port:
            return None

        current = await self._get_installed_version(port)
        if current == "unknown":
            return None

        github = GitHubClient()
        try:
            release = await github.get_latest_release(port.repo)
            if release.tag_name != current:
                return (current, release.tag_name)
        finally:
            await github.close()

        return None

    async def update(self, port_id: str) -> bool:
        """Update a port if an update is available.

        Returns:
            True if updated, False if already up to date
        """
        update_info = await self.check_update(port_id)
        if not update_info:
            return False

        current, new = update_info
        self.progress.status(f"Updating {port_id}: {current} -> {new}")

        # Back up mods directory
        port_dir = self.config.ports_dir / port_id
        mods_backup = None
        if (port_dir / "mods").exists():
            mods_backup = port_dir / "mods_backup"
            shutil.move(str(port_dir / "mods"), str(mods_backup))

        # Re-install
        await self.install(port_id, with_mods=False, add_to_steam=False, fetch_artwork=False)

        # Restore mods
        if mods_backup:
            if (port_dir / "mods").exists():
                shutil.rmtree(port_dir / "mods")
            shutil.move(str(mods_backup), str(port_dir / "mods"))

        return True


# Synchronous wrappers for CLI use
def install_port_sync(
    port_id: str,
    with_mods: bool = True,
    add_to_steam: bool = True,
    fetch_artwork: bool = True,
    on_status: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Synchronous wrapper for installing a port."""

    async def _install():
        progress = ProgressReporter(on_status, on_progress)
        installer = PortInstaller(progress=progress)
        return await installer.install(port_id, with_mods, add_to_steam, fetch_artwork)

    return asyncio.run(_install())


def check_updates_sync() -> list[tuple[str, str, str]]:
    """Check all installed ports for updates.

    Returns:
        List of (port_id, current_version, new_version) tuples
    """

    async def _check():
        config = Config.load()
        library = Library.load(config.pier_dir)
        installer = PortInstaller(config=config, library=library)

        updates = []
        for port_id in library.installed_ports:
            update_info = await installer.check_update(port_id)
            if update_info:
                updates.append((port_id, update_info[0], update_info[1]))
        return updates

    return asyncio.run(_check())
