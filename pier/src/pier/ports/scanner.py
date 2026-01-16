"""Installed port scanning and version tracking."""

import json
from dataclasses import dataclass
from pathlib import Path

from pier.ports.registry import PORTS, Port
from pier.ports.steam import is_port_in_steam

VERSION_FILE = ".pier-version"
METADATA_FILE = "ports.json"


@dataclass
class InstalledPort:
    """An installed PC port."""

    id: str
    port: Port
    install_dir: Path
    version: str | None
    rom_path: Path | None
    in_steam: bool


def get_installed_version(port_dir: Path) -> str | None:
    """Read the installed version of a port.

    Checks for .pier-version file first, then VERSION file.

    Args:
        port_dir: The port's installation directory.

    Returns:
        Version string or None if not found.
    """
    # Check .pier-version (written by pier)
    pier_version = port_dir / VERSION_FILE
    if pier_version.exists():
        try:
            return pier_version.read_text().strip()
        except OSError:
            pass

    # Check VERSION file (may be included in release)
    version_file = port_dir / "VERSION"
    if version_file.exists():
        try:
            return version_file.read_text().strip()
        except OSError:
            pass

    return None


def save_installed_version(port_dir: Path, version: str) -> None:
    """Save the installed version of a port.

    Args:
        port_dir: The port's installation directory.
        version: The version string to save.
    """
    pier_version = port_dir / VERSION_FILE
    pier_version.write_text(version)


def find_port_rom(port_dir: Path) -> Path | None:
    """Find the ROM file for an installed port.

    Args:
        port_dir: The port's installation directory.

    Returns:
        Path to the ROM file, or None if not found.
    """
    rom_dir = port_dir / "rom"
    if not rom_dir.exists():
        return None

    # Return first file in rom directory
    for rom_file in rom_dir.iterdir():
        if rom_file.is_file():
            return rom_file

    return None


def _find_executable(port_dir: Path, executable_name: str) -> bool:
    """Check if a port's executable exists.

    Checks both the port directory root and one level of subdirectories.

    Args:
        port_dir: The port's installation directory.
        executable_name: Name of the executable to find.

    Returns:
        True if the executable was found.
    """
    # Check root directory
    if (port_dir / executable_name).exists():
        return True

    # Check subdirectories (one level deep)
    try:
        for subdir in port_dir.iterdir():
            if subdir.is_dir() and not subdir.name.startswith("."):
                if (subdir / executable_name).exists():
                    return True
    except OSError:
        # Permission error or other issue - treat as not found
        pass

    return False


def scan_installed_ports(ports_dir: Path) -> list[InstalledPort]:
    """Scan for installed ports.

    Args:
        ports_dir: Base directory for installed ports.

    Returns:
        List of installed ports found.
    """
    installed = []

    if not ports_dir.exists():
        return installed

    for port_id, port in PORTS.items():
        port_dir = ports_dir / port_id

        if not port_dir.exists():
            continue

        # Check if it has an executable (indicates actual installation)
        if not _find_executable(port_dir, port.executable_name):
            continue

        installed.append(
            InstalledPort(
                id=port_id,
                port=port,
                install_dir=port_dir,
                version=get_installed_version(port_dir),
                rom_path=find_port_rom(port_dir),
                in_steam=is_port_in_steam(port_id),
            )
        )

    return installed


def get_installed_port(port_id: str, ports_dir: Path) -> InstalledPort | None:
    """Get a specific installed port.

    Args:
        port_id: The port ID to check.
        ports_dir: Base directory for installed ports.

    Returns:
        InstalledPort if found, None otherwise.
    """
    if port_id not in PORTS:
        return None

    port = PORTS[port_id]
    port_dir = ports_dir / port_id

    if not port_dir.exists():
        return None

    # Check if it has an executable
    if not _find_executable(port_dir, port.executable_name):
        return None

    return InstalledPort(
        id=port_id,
        port=port,
        install_dir=port_dir,
        version=get_installed_version(port_dir),
        rom_path=find_port_rom(port_dir),
        in_steam=is_port_in_steam(port_id),
    )


def load_ports_metadata(data_dir: Path) -> dict:
    """Load ports metadata from disk.

    Args:
        data_dir: The pier data directory.

    Returns:
        Metadata dictionary.
    """
    metadata_path = data_dir / "metadata" / METADATA_FILE
    if not metadata_path.exists():
        return {}

    try:
        return json.loads(metadata_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_ports_metadata(data_dir: Path, metadata: dict) -> None:
    """Save ports metadata to disk.

    Args:
        data_dir: The pier data directory.
        metadata: Metadata dictionary to save.
    """
    metadata_dir = data_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = metadata_dir / METADATA_FILE
    metadata_path.write_text(json.dumps(metadata, indent=2))
