"""Unified game search across all sources."""

from pathlib import Path
from typing import TYPE_CHECKING

from pier.games.model import Game, GameSource, GameType, SearchResult
from pier.ports.registry import PORTS, Port
from pier.ports.scanner import scan_installed_ports
from pier.ports.updater import check_for_update
from pier.roms.myrient import MyrientClient, MyrientError, MyrientFile
from pier.roms.scanner import clean_rom_name, scan_roms
from pier.roms.systems import SYSTEMS

if TYPE_CHECKING:
    from pier.roms.scanner import Game as RomGame


def _rom_to_game(rom: "RomGame", in_steam: bool = False) -> Game:
    """Convert a ROM scanner Game to unified Game."""
    return Game(
        id=rom.id,
        name=rom.display_name,
        type=GameType.ROM,
        source=GameSource.LOCAL,
        system=rom.system.id,
        installed=True,
        in_steam=in_steam,
        metadata={
            "path": str(rom.path),
            "filename": rom.filename,
            "full_name": rom.name,
        },
    )


def _myrient_to_game(file: MyrientFile, system_id: str) -> Game:
    """Convert a Myrient file to unified Game."""
    # Extract clean name from filename
    stem = Path(file.name).stem
    display_name = clean_rom_name(stem)

    return Game(
        id=f"myrient:{system_id}:{file.name}",
        name=display_name,
        type=GameType.ROM,
        source=GameSource.MYRIENT,
        system=system_id,
        installed=False,
        in_steam=False,
        metadata={
            "url": file.url,
            "size": file.size,
            "filename": file.name,
            "full_name": stem,
        },
    )


def _port_def_to_game(port: Port, installed: bool, in_steam: bool, version: str | None, update_available: bool) -> Game:
    """Convert a Port definition to unified Game."""
    return Game(
        id=f"port:{port.id}",
        name=port.name,
        type=GameType.PORT,
        source=GameSource.LOCAL if installed else GameSource.REGISTRY,
        system="port",
        description=f"{port.type.value.replace('_', ' ').title()} port",
        version=version,
        installed=installed,
        in_steam=in_steam,
        update_available=update_available,
        metadata={
            "port_id": port.id,
            "github_repo": port.github_repo,
            "rom_system": port.system,
            "has_enhancements": len(port.enhancements) > 0,
        },
    )


def search_installed(
    roms_dir: Path,
    ports_dir: Path,
    query: str | None = None,
    check_updates: bool = False,
    github_token: str | None = None,
) -> SearchResult:
    """Search installed games (ROMs and ports).

    Args:
        roms_dir: Directory containing ROM files.
        ports_dir: Directory containing installed ports.
        query: Optional search query to filter results.
        check_updates: Whether to check for port updates.
        github_token: GitHub token for update checks.

    Returns:
        SearchResult with installed games.
    """
    result = SearchResult()
    query_lower = query.lower() if query else None

    # Scan ROMs
    from pier.roms.scanner import Game as Game_ROM
    roms = scan_roms(roms_dir)
    for rom in roms:
        if query_lower and query_lower not in rom.display_name.lower():
            continue
        result.installed_roms.append(_rom_to_game(rom, rom.in_steam))

    # Scan ports
    installed_ports = scan_installed_ports(ports_dir)
    for installed in installed_ports:
        if query_lower and query_lower not in installed.port.name.lower():
            continue

        update_available = False
        if check_updates:
            try:
                update_available = check_for_update(installed, github_token) is not None
            except Exception:
                pass

        result.installed_ports.append(
            _port_def_to_game(
                installed.port,
                installed=True,
                in_steam=installed.in_steam,
                version=installed.version,
                update_available=update_available,
            )
        )

    return result


def search_available_ports(query: str | None = None, installed_ids: set[str] | None = None) -> list[Game]:
    """Search available (not installed) ports.

    Args:
        query: Optional search query.
        installed_ids: Set of installed port IDs to exclude.

    Returns:
        List of available port Games.
    """
    installed_ids = installed_ids or set()
    query_lower = query.lower() if query else None
    results = []

    for port_id, port in PORTS.items():
        if port_id in installed_ids:
            continue

        if query_lower:
            # Match against port name, ROM search name, or SteamGridDB name
            searchable = [port.name.lower(), port.rom_search_name.lower()]
            if port.steamgriddb_name:
                searchable.append(port.steamgriddb_name.lower())

            if not any(query_lower in s for s in searchable):
                continue

        results.append(
            _port_def_to_game(
                port,
                installed=False,
                in_steam=False,
                version=None,
                update_available=False,
            )
        )

    return results


def search_myrient(
    query: str,
    system_filter: str | None = None,
    limit: int = 10,
) -> list[Game]:
    """Search Myrient for ROMs.

    Args:
        query: Search query.
        system_filter: Optional system ID to filter.
        limit: Maximum results per system.

    Returns:
        List of available ROM Games from Myrient.
    """
    results = []
    systems_to_search = (
        [SYSTEMS[system_filter]] if system_filter and system_filter in SYSTEMS
        else list(SYSTEMS.values())
    )

    with MyrientClient() as client:
        for system in systems_to_search:
            if not system.myrient_path:
                continue

            try:
                files = client.search(system, query)
                for file in files[:limit]:
                    results.append(_myrient_to_game(file, system.id))
            except MyrientError:
                # Skip systems that fail (network issues, etc.)
                continue

    return results


def search_games(
    query: str | None,
    roms_dir: Path,
    ports_dir: Path,
    include_myrient: bool = True,
    system_filter: str | None = None,
    myrient_limit: int = 5,
    check_updates: bool = False,
    github_token: str | None = None,
) -> SearchResult:
    """Unified search across all game sources.

    Args:
        query: Search query. If None, returns only installed games.
        roms_dir: Directory containing ROM files.
        ports_dir: Directory containing installed ports.
        include_myrient: Whether to search Myrient for ROMs.
        system_filter: Optional system ID to filter ROM searches.
        myrient_limit: Maximum Myrient results per system.
        check_updates: Whether to check for port updates.
        github_token: GitHub token for API calls.

    Returns:
        SearchResult with games from all sources.
    """
    # Start with installed games
    result = search_installed(
        roms_dir=roms_dir,
        ports_dir=ports_dir,
        query=query,
        check_updates=check_updates,
        github_token=github_token,
    )

    # Get installed port IDs for filtering
    installed_port_ids = {g.metadata["port_id"] for g in result.installed_ports}

    # Search available ports
    result.available_ports = search_available_ports(query, installed_port_ids)

    # Search Myrient if query provided and not filtered to ports only
    if query and include_myrient:
        # Get installed ROM filenames to filter duplicates
        installed_filenames = {
            g.metadata.get("filename", "").lower() for g in result.installed_roms
        }

        myrient_results = search_myrient(query, system_filter, myrient_limit)

        # Filter out ROMs we already have installed
        for game in myrient_results:
            filename = game.metadata.get("filename", "").lower()
            if filename not in installed_filenames:
                result.available_roms.append(game)

    return result
