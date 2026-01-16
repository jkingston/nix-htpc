"""PC ports management module."""

from pier.ports.enhancements import (
    EnhancementError,
    download_enhancement,
    get_installed_enhancements,
    remove_enhancement,
)
from pier.ports.github import GitHubAsset, GitHubClient, GitHubError, GitHubRelease
from pier.ports.installer import (
    InstallerError,
    InstallResult,
    find_matching_rom,
    find_or_download_rom,
    install_port,
    remove_port,
)
from pier.ports.registry import PORTS, Enhancement, Port, PortType, get_port, list_ports
from pier.ports.scanner import (
    InstalledPort,
    get_installed_port,
    save_installed_version,
    scan_installed_ports,
)
from pier.ports.steam import (
    is_port_in_steam,
    remove_port_from_steam,
    sync_port_to_steam,
)
from pier.ports.updater import check_all_for_updates, check_for_update

__all__ = [
    # Registry
    "PORTS",
    "Enhancement",
    "Port",
    "PortType",
    "get_port",
    "list_ports",
    # Enhancements
    "EnhancementError",
    "download_enhancement",
    "get_installed_enhancements",
    "remove_enhancement",
    # GitHub
    "GitHubAsset",
    "GitHubClient",
    "GitHubError",
    "GitHubRelease",
    # Installer
    "InstallResult",
    "InstallerError",
    "find_matching_rom",
    "find_or_download_rom",
    "install_port",
    "remove_port",
    # Scanner
    "InstalledPort",
    "get_installed_port",
    "scan_installed_ports",
    "save_installed_version",
    # Steam
    "is_port_in_steam",
    "remove_port_from_steam",
    "sync_port_to_steam",
    # Updater
    "check_all_for_updates",
    "check_for_update",
]
