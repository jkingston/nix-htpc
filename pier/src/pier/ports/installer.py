"""Port installation logic.

This module handles downloading, extracting, and setting up PC ports.
"""

import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pier.ports.github import GitHubClient, GitHubError
from pier.ports.registry import Port, PortType
from pier.roms.hashing import N64Format, compute_sha1, convert_to_z64, detect_n64_format
from pier.roms.myrient import MyrientClient, MyrientError
from pier.roms.systems import SYSTEMS


@dataclass
class InstallResult:
    """Result of a port installation."""

    success: bool
    port: Port
    install_dir: Path | None = None
    version: str | None = None
    rom_path: Path | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class InstallerError(Exception):
    """Error during port installation."""

    pass


def find_matching_rom(
    port: Port,
    roms_dir: Path,
    ports_dir: Path,
) -> Path | None:
    """Find a ROM matching the port's hash requirements.

    Searches in order:
    1. Port's local rom/ directory
    2. User's roms_dir collection

    Args:
        port: The port to find a ROM for.
        roms_dir: User's ROM collection directory.
        ports_dir: Base directory for installed ports.

    Returns:
        Path to matching ROM, or None if not found.
    """
    # If port has no hash requirements, we can't verify
    if not port.required_hashes:
        return None

    # 1. Check port's local ROM directory first
    port_rom_dir = ports_dir / port.id / "rom"
    if port_rom_dir.exists():
        for rom_file in port_rom_dir.iterdir():
            if not rom_file.is_file():
                continue
            sha1 = compute_sha1(rom_file)
            if sha1.lower() in port.required_hashes:
                return rom_file

    # 2. Check user's ROM collection
    system = SYSTEMS.get(port.system)
    if system:
        system_dir = roms_dir / system.id
        if system_dir.exists():
            for rom_file in system_dir.iterdir():
                if not rom_file.is_file():
                    continue
                sha1 = compute_sha1(rom_file)
                if sha1.lower() in port.required_hashes:
                    return rom_file

    return None


def download_rom_for_port(
    port: Port,
    ports_dir: Path,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> Path | None:
    """Download a ROM from Myrient for a port.

    Args:
        port: The port to download ROM for.
        ports_dir: Base directory for installed ports.
        progress_callback: Optional callback(status, downloaded, total).

    Returns:
        Path to downloaded ROM, or None if not found/downloaded.

    Raises:
        InstallerError: If download fails.
    """
    if not port.required_hashes:
        return None

    system = SYSTEMS.get(port.system)
    if not system:
        raise InstallerError(f"Unknown system: {port.system}")

    # Create port's ROM directory
    port_rom_dir = ports_dir / port.id / "rom"
    port_rom_dir.mkdir(parents=True, exist_ok=True)

    try:
        with MyrientClient() as myrient:
            # Search for the ROM
            if progress_callback:
                progress_callback(f"Searching Myrient for {port.rom_search_name}...", 0, 0)

            matches = myrient.search(system, port.rom_search_name)
            if not matches:
                return None

            # Try each match until we find one with correct hash
            for myrient_file in matches[:5]:  # Limit to top 5 matches
                if progress_callback:
                    progress_callback(f"Downloading {myrient_file.name}...", 0, myrient_file.size)

                # Download to temp directory first
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)

                    # Capture current file name in closure
                    current_file_name = myrient_file.name

                    def download_progress(
                        downloaded: int, total: int, file_name: str = current_file_name
                    ) -> None:
                        if progress_callback:
                            progress_callback(f"Downloading {file_name}...", downloaded, total)

                    rom_path = myrient.download(
                        myrient_file,
                        temp_path,
                        progress_callback=download_progress,
                        extract=True,
                    )

                    # Verify hash
                    sha1 = compute_sha1(rom_path)
                    if sha1.lower() in port.required_hashes:
                        # Convert to z64 format if needed for N64 ROMs
                        if port.system == "n64":
                            fmt = detect_n64_format(rom_path)
                            if fmt != N64Format.Z64 and fmt != N64Format.UNKNOWN:
                                if progress_callback:
                                    progress_callback("Converting to z64 format...", 0, 0)
                                convert_to_z64(rom_path)

                        # Move to port's ROM directory
                        dest_path = port_rom_dir / rom_path.name
                        shutil.move(str(rom_path), str(dest_path))
                        return dest_path

            # No matching ROM found
            return None

    except MyrientError as e:
        raise InstallerError(f"Failed to download ROM: {e}") from e


def find_or_download_rom(
    port: Port,
    roms_dir: Path,
    ports_dir: Path,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> Path | None:
    """Find a matching ROM locally, or download from Myrient.

    Args:
        port: The port to find/download ROM for.
        roms_dir: User's ROM collection directory.
        ports_dir: Base directory for installed ports.
        progress_callback: Optional callback(status, downloaded, total).

    Returns:
        Path to ROM, or None if not found/downloaded.
    """
    # First try to find locally
    rom_path = find_matching_rom(port, roms_dir, ports_dir)
    if rom_path:
        return rom_path

    # Download from Myrient
    return download_rom_for_port(port, ports_dir, progress_callback)


def _is_safe_path(base_dir: Path, member_path: str) -> bool:
    """Check if an archive member path is safe to extract.

    Prevents path traversal attacks by ensuring the resolved path
    stays within the destination directory.

    Args:
        base_dir: The destination directory for extraction.
        member_path: The path from the archive member.

    Returns:
        True if the path is safe to extract.
    """
    member = Path(member_path)

    # Reject absolute paths
    if member.is_absolute():
        return False

    # Reject paths with .. components
    if ".." in member.parts:
        return False

    # Reject paths starting with / after normalization
    normalized = str(member).lstrip("/\\")
    if normalized != str(member):
        return False

    # Ensure resolved path is within base_dir using is_relative_to (Python 3.9+)
    full_path = (base_dir / member_path).resolve()
    base_resolved = base_dir.resolve()

    return full_path.is_relative_to(base_resolved)


def _safe_extract_zip(zf: zipfile.ZipFile, dest_dir: Path) -> None:
    """Safely extract a ZIP archive with path traversal protection.

    Args:
        zf: The ZipFile object to extract from.
        dest_dir: Directory to extract to.

    Raises:
        InstallerError: If a dangerous path is detected.
    """
    for member in zf.infolist():
        if not _is_safe_path(dest_dir, member.filename):
            msg = f"Unsafe path in archive: {member.filename}"
            raise InstallerError(msg)
        zf.extract(member, dest_dir)


def _safe_extract_tar(tf: tarfile.TarFile, dest_dir: Path) -> None:
    """Safely extract a TAR archive with path traversal protection.

    Args:
        tf: The TarFile object to extract from.
        dest_dir: Directory to extract to.

    Raises:
        InstallerError: If a dangerous path is detected.
    """
    for member in tf.getmembers():
        # Reject symlinks and hardlinks (can point outside dest_dir)
        if member.issym() or member.islnk():
            msg = f"Unsafe link in archive: {member.name}"
            raise InstallerError(msg)

        if not _is_safe_path(dest_dir, member.name):
            msg = f"Unsafe path in archive: {member.name}"
            raise InstallerError(msg)
        tf.extract(member, dest_dir)


def extract_archive(archive_path: Path, dest_dir: Path) -> None:
    """Extract a ZIP or tar.gz archive safely.

    Validates all member paths to prevent path traversal attacks.

    Args:
        archive_path: Path to the archive file.
        dest_dir: Directory to extract to.

    Raises:
        InstallerError: If archive format is unknown or contains unsafe paths.
    """
    name = archive_path.name.lower()

    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            _safe_extract_zip(zf, dest_dir)
    elif name.endswith(".tar.gz") or name.endswith(".tgz"):
        with tarfile.open(archive_path, "r:gz") as tf:
            _safe_extract_tar(tf, dest_dir)
    elif name.endswith(".tar"):
        with tarfile.open(archive_path, "r") as tf:
            _safe_extract_tar(tf, dest_dir)
    else:
        msg = f"Unknown archive format: {archive_path.name}"
        raise InstallerError(msg)


def generate_harbour_masters_assets(
    port: Port,
    install_dir: Path,
    rom_path: Path,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> bool:
    """Generate OTR assets for a Harbour Masters port.

    Runs the port executable with the ROM to generate the .otr file.

    Args:
        port: The port being installed.
        install_dir: Port installation directory.
        rom_path: Path to the source ROM.
        progress_callback: Optional callback for status updates.

    Returns:
        True if asset generation succeeded.
    """
    executable = install_dir / port.executable_name

    if not executable.exists():
        # Try to find executable in subdirectory
        for subdir in install_dir.iterdir():
            if subdir.is_dir():
                candidate = subdir / port.executable_name
                if candidate.exists():
                    executable = candidate
                    break

    if not executable.exists():
        raise InstallerError(f"Executable not found: {port.executable_name}")

    # Make executable
    executable.chmod(executable.stat().st_mode | 0o111)

    # Copy ROM to install directory for asset generation
    rom_dest = install_dir / rom_path.name
    shutil.copy2(rom_path, rom_dest)

    if progress_callback:
        progress_callback("Generating game assets (this may take a moment)...", 0, 0)

    try:
        # Run the executable to generate OTR
        # Harbour Masters ports detect the ROM and generate assets on first run
        subprocess.run(
            [str(executable), "--generate-otr"],
            cwd=install_dir,
            capture_output=True,
            timeout=300,  # 5 minute timeout
            check=False,  # Don't raise on non-zero exit (some ports don't have this flag)
        )

        # Check for OTR file generation
        otr_files = list(install_dir.glob("*.otr")) + list(install_dir.glob("*.o2r"))
        if otr_files:
            # Clean up the ROM copy (OTR now contains the assets)
            rom_dest.unlink(missing_ok=True)
            return True

        # No OTR files generated - asset generation failed
        # Leave ROM copy in place for manual troubleshooting
        return False

    except subprocess.TimeoutExpired as e:
        raise InstallerError("Asset generation timed out") from e
    except subprocess.SubprocessError as e:
        raise InstallerError(f"Asset generation failed: {e}") from e


def install_port(
    port: Port,
    roms_dir: Path,
    ports_dir: Path,
    github_token: str | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> InstallResult:
    """Install a PC port.

    Args:
        port: The port to install.
        roms_dir: User's ROM collection directory.
        ports_dir: Base directory for installed ports.
        github_token: Optional GitHub token for API rate limits.
        progress_callback: Optional callback(status, downloaded, total).

    Returns:
        InstallResult with success status and details.
    """
    result = InstallResult(success=False, port=port)
    install_dir = ports_dir / port.id

    try:
        # 1. Find or download ROM
        if progress_callback:
            progress_callback("Finding ROM...", 0, 0)

        rom_path = find_or_download_rom(port, roms_dir, ports_dir, progress_callback)
        if not rom_path and port.required_hashes:
            result.errors.append(
                f"Could not find ROM matching required hash for {port.name}. "
                f"Search Myrient for: {port.rom_search_name}"
            )
            return result

        result.rom_path = rom_path

        # 2. Download port release from GitHub
        if progress_callback:
            progress_callback(f"Fetching latest release from {port.github_repo}...", 0, 0)

        with GitHubClient(token=github_token) as github:
            try:
                release = github.get_latest_release(port.github_repo)
            except GitHubError as e:
                result.errors.append(f"Failed to get release: {e}")
                return result

            result.version = release.tag_name

            # Find Linux asset
            asset = github.find_asset(release, port.linux_asset_pattern)
            if not asset:
                result.errors.append(
                    f"No Linux asset matching '{port.linux_asset_pattern}' "
                    f"in release {release.tag_name}"
                )
                return result

            # Download to temp location
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                def download_progress(downloaded: int, total: int) -> None:
                    if progress_callback:
                        progress_callback(f"Downloading {asset.name}...", downloaded, total)

                archive_path = github.download_asset(asset, temp_path, download_progress)

                # 3. Extract to install directory
                if progress_callback:
                    progress_callback("Extracting...", 0, 0)

                # Clean existing installation
                if install_dir.exists():
                    shutil.rmtree(install_dir)
                install_dir.mkdir(parents=True, exist_ok=True)

                extract_archive(archive_path, install_dir)

        # 4. Generate assets based on port type
        if port.type == PortType.HARBOUR_MASTERS and rom_path:
            generate_harbour_masters_assets(port, install_dir, rom_path, progress_callback)
        elif port.type == PortType.OPENGOAL and rom_path:
            # OpenGOAL asset extraction would go here
            result.warnings.append("OpenGOAL asset extraction not yet implemented")
        elif port.type == PortType.DIRECT_PORT and rom_path:
            # Direct port ROM setup would go here
            result.warnings.append("Direct port ROM setup not yet implemented")

        # 5. Save version and mark success
        if result.version:
            version_file = install_dir / ".pier-version"
            version_file.write_text(result.version)

        result.success = True
        result.install_dir = install_dir

    except InstallerError as e:
        result.errors.append(str(e))
    except Exception as e:
        result.errors.append(f"Unexpected error: {e}")

    return result


def remove_port(
    port_id: str,
    ports_dir: Path,
    remove_rom: bool = True,
    remove_from_steam: bool = True,
) -> bool:
    """Remove an installed port.

    Args:
        port_id: The port ID to remove.
        ports_dir: Base directory for installed ports.
        remove_rom: If True, also remove the ROM file.
        remove_from_steam: If True, remove the Steam shortcut.

    Returns:
        True if the port was removed, False if not found.

    Raises:
        ValueError: If port_id is not a known port.
    """
    from pier.ports.registry import PORTS
    from pier.ports.steam import remove_port_from_steam

    # Validate port_id to prevent removing arbitrary directories
    if port_id not in PORTS:
        raise ValueError(f"Unknown port: {port_id}")

    port_dir = ports_dir / port_id

    if not port_dir.exists():
        return False

    # Remove from Steam first
    if remove_from_steam:
        remove_port_from_steam(port_id)

    # Optionally preserve the ROM by removing everything except rom/ directory
    rom_dir = port_dir / "rom"
    if not remove_rom and rom_dir.exists():
        # Remove all files and directories except rom/
        for item in port_dir.iterdir():
            if item.name == "rom":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    else:
        # Remove entire port directory
        shutil.rmtree(port_dir)

    return True
