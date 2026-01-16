"""Pier CLI - Game management for NixOS HTPC."""

from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pier import __version__
from pier.config import Config
from pier.games import GameSource, GameType, search_games
from pier.steam.artwork import ArtworkType
from pier.steam.paths import find_shortcuts_vdf, find_steam_userdata, is_steam_running
from pier.steam.shortcuts import (
    ShortcutStatus,
    find_shortcut,
    get_all_shortcuts,
    get_shortcut_details,
    get_shortcut_status,
    remove_shortcut,
    shortcut_matches,
)
from pier.steam.steamgriddb import SteamGridDBClient, SteamGridDBError
from pier.steam.sync import sync_games as steam_sync_games
from pier.steam.vdf import load_shortcuts

console = Console()


# =============================================================================
# Main CLI Group and Dashboard
# =============================================================================


@click.group(invoke_without_command=True)
@click.version_option(version=__version__)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Pier - Game management for NixOS HTPC.

    Run without arguments to see status dashboard.
    """
    if ctx.invoked_subcommand is None:
        _show_dashboard()


def _show_dashboard() -> None:
    """Show the status dashboard."""
    config = Config.load()

    # Get installed games
    result = search_games(
        query=None,
        roms_dir=config.roms_dir,
        ports_dir=config.ports_dir,
        include_myrient=False,
        check_updates=True,
        github_token=config.github_token,
    )

    # Count games in Steam
    roms_in_steam = sum(1 for g in result.installed_roms if g.in_steam)
    ports_in_steam = sum(1 for g in result.installed_ports if g.in_steam)
    ports_with_updates = sum(1 for g in result.installed_ports if g.update_available)

    # Check Steam status
    steam_running = is_steam_running()
    steam_status = "[green]Running[/green]" if steam_running else "[yellow]Not running[/yellow]"

    # Build status panel
    status_lines = []
    status_lines.append(f"[bold]ROMs:[/bold] {len(result.installed_roms)} games ({roms_in_steam} in Steam)")
    status_lines.append(f"[bold]Ports:[/bold] {len(result.installed_ports)} installed ({ports_in_steam} in Steam)")
    status_lines.append(f"[bold]Steam:[/bold] {steam_status}")

    console.print(Panel("\n".join(status_lines), title="[bold cyan]Pier Status[/bold cyan]", expand=False))
    console.print()

    # Quick actions
    actions = []

    # Sync action
    roms_not_synced = len(result.installed_roms) - roms_in_steam
    ports_not_synced = len(result.installed_ports) - ports_in_steam
    if roms_not_synced > 0 or ports_not_synced > 0:
        total_not_synced = roms_not_synced + ports_not_synced
        actions.append(f"[cyan]pier sync[/cyan]    - Add {total_not_synced} games to Steam")

    # Update action
    if ports_with_updates > 0:
        port_names = [g.name for g in result.installed_ports if g.update_available]
        actions.append(f"[cyan]pier update[/cyan]  - Update {', '.join(port_names[:2])}")

    if actions:
        console.print("[bold]Quick Actions:[/bold]")
        for action in actions:
            console.print(f"  {action}")
        console.print()

    # Hint
    console.print("[dim]Run 'pier search' to see installed games, or 'pier search <query>' to find new games.[/dim]")


# =============================================================================
# Search Command
# =============================================================================


@cli.command()
@click.argument("query", required=False)
@click.option("--system", "-s", help="Filter by system (e.g., n64, ps2)")
@click.option("--no-myrient", is_flag=True, help="Don't search Myrient for ROMs")
def search(query: str | None, system: str | None, no_myrient: bool) -> None:
    """Search for games.

    Without QUERY, lists installed games.
    With QUERY, searches installed games, available ports, and Myrient.
    """
    config = Config.load()

    result = search_games(
        query=query,
        roms_dir=config.roms_dir,
        ports_dir=config.ports_dir,
        include_myrient=not no_myrient and query is not None,
        system_filter=system,
        check_updates=True,
        github_token=config.github_token,
    )

    if query is None:
        # No query - show installed games
        _show_installed_games(result)
    else:
        # Query provided - show search results
        _show_search_results(result, query)


def _show_installed_games(result) -> None:
    """Show installed games."""
    if result.total_installed == 0:
        console.print("[yellow]No games installed.[/yellow]")
        console.print("[dim]Use 'pier search <game>' to find games to install.[/dim]")
        return

    # Show ports first
    if result.installed_ports:
        table = Table(title=f"Installed Ports ({len(result.installed_ports)})")
        table.add_column("Name", style="cyan")
        table.add_column("Version")
        table.add_column("Steam", justify="center")
        table.add_column("Status")

        for game in result.installed_ports:
            steam = "[green]✓[/green]" if game.in_steam else "[dim]-[/dim]"
            status = "[yellow]Update available[/yellow]" if game.update_available else "[green]Up to date[/green]"
            table.add_row(game.name, game.version or "?", steam, status)

        console.print(table)
        console.print()

    # Show ROMs grouped by system
    if result.installed_roms:
        # Group by system
        by_system: dict[str, list] = {}
        for game in result.installed_roms:
            by_system.setdefault(game.system, []).append(game)

        for system_id in sorted(by_system.keys()):
            games = by_system[system_id]
            from pier.roms.systems import SYSTEMS
            system_name = SYSTEMS[system_id].name if system_id in SYSTEMS else system_id

            table = Table(title=f"{system_name} ({len(games)} ROMs)")
            table.add_column("Name", style="cyan")
            table.add_column("Steam", justify="center")

            for game in sorted(games, key=lambda g: g.name.lower()):
                steam = "[green]✓[/green]" if game.in_steam else "[dim]-[/dim]"
                table.add_row(game.name, steam)

            console.print(table)
            console.print()


def _show_search_results(result, query: str) -> None:
    """Show search results."""
    if result.total == 0:
        console.print(f"[yellow]No results found for '{query}'.[/yellow]")
        return

    console.print(f"[bold]Search results for '{query}':[/bold]")
    console.print()

    # Installed ports
    if result.installed_ports:
        console.print("[bold green]INSTALLED PORTS[/bold green]")
        for game in result.installed_ports:
            status = "[yellow]update available[/yellow]" if game.update_available else ""
            console.print(f"  [cyan]{game.name}[/cyan] {status}")
        console.print()

    # Installed ROMs
    if result.installed_roms:
        console.print("[bold green]INSTALLED ROMS[/bold green]")
        for game in result.installed_roms:
            from pier.roms.systems import SYSTEMS
            system_name = SYSTEMS[game.system].name if game.system in SYSTEMS else game.system
            console.print(f"  [cyan]{game.name}[/cyan] ({system_name})")
        console.print()

    # Available ports
    if result.available_ports:
        console.print("[bold blue]AVAILABLE PORTS[/bold blue]")
        for game in result.available_ports:
            console.print(f"  {game.name} - {game.description}")
        console.print()

    # Available ROMs from Myrient
    if result.available_roms:
        console.print("[bold blue]AVAILABLE ON MYRIENT[/bold blue]")
        for game in result.available_roms:
            from pier.roms.systems import SYSTEMS
            system_name = SYSTEMS[game.system].name if game.system in SYSTEMS else game.system
            size_mb = game.metadata.get("size", 0) / (1024 * 1024)
            console.print(f"  {game.name} ({system_name}) [{size_mb:.1f} MB]")
        console.print()

    # Installation hint
    installable = result.all_installable()
    if installable:
        console.print(f"[dim]Use 'pier install \"{query}\"' to install a game.[/dim]")


# =============================================================================
# Install Command
# =============================================================================


@cli.command()
@click.argument("query")
@click.option("--no-steam", is_flag=True, help="Don't add to Steam")
@click.option("--no-enhancements", is_flag=True, help="Don't install HD textures/enhancements")
@click.option("--system", "-s", help="Filter to specific system for ROM install")
def install(query: str, no_steam: bool, no_enhancements: bool, system: str | None) -> None:
    """Install a game.

    Searches for QUERY across ports and ROMs, lets you choose which to install,
    then downloads, installs, adds to Steam, and fetches artwork.
    """
    config = Config.load()

    # Search for matching games
    result = search_games(
        query=query,
        roms_dir=config.roms_dir,
        ports_dir=config.ports_dir,
        include_myrient=True,
        system_filter=system,
        myrient_limit=10,
    )

    # Build list of installable options
    options = []

    # Available ports first
    for game in result.available_ports:
        options.append(("port", game))

    # Available ROMs from Myrient
    for game in result.available_roms:
        options.append(("rom", game))

    # Also show installed matches (for re-install or info)
    already_installed = result.installed_ports + result.installed_roms

    if not options and not already_installed:
        console.print(f"[yellow]No installable games found for '{query}'.[/yellow]")
        return

    if not options:
        console.print(f"[yellow]All matching games are already installed:[/yellow]")
        for game in already_installed:
            console.print(f"  [cyan]{game.name}[/cyan]")
        console.print("\n[dim]Use 'pier info <game>' for details or 'pier update' to update ports.[/dim]")
        return

    # Show options and let user choose
    console.print(f"[bold]Found {len(options)} installable game(s) for '{query}':[/bold]")
    console.print()

    # Group by type for display
    port_options = [(i, g) for i, (t, g) in enumerate(options, 1) if t == "port"]
    rom_options = [(i, g) for i, (t, g) in enumerate(options, 1) if t == "rom"]

    if port_options:
        console.print("[bold]PORTS[/bold]")
        for idx, game in port_options:
            console.print(f"  [{idx}] {game.name} - {game.description}")
        console.print()

    if rom_options:
        console.print("[bold]ROMS[/bold]")
        for idx, game in rom_options:
            from pier.roms.systems import SYSTEMS
            system_name = SYSTEMS[game.system].name if game.system in SYSTEMS else game.system
            console.print(f"  [{idx}] {game.name} ({system_name})")
        console.print()

    # Get user choice
    if len(options) == 1:
        choice = 1
        console.print(f"[dim]Auto-selecting the only option.[/dim]")
    else:
        try:
            choice_str = click.prompt("Which would you like to install?", type=str)
            choice = int(choice_str)
            if choice < 1 or choice > len(options):
                console.print("[red]Invalid choice.[/red]")
                return
        except (ValueError, click.Abort):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    game_type, game = options[choice - 1]

    # Install based on type
    if game_type == "port":
        _install_port(game, config, no_steam, no_enhancements)
    else:
        _install_rom(game, config, no_steam)


def _install_port(game, config: Config, no_steam: bool, no_enhancements: bool) -> None:
    """Install a PC port."""
    from pier.ports import PORTS, InstallerError, install_port

    port_id = game.metadata["port_id"]
    port = PORTS[port_id]

    console.print(f"\n[bold]Installing {port.name}...[/bold]")

    def progress_callback(status: str, downloaded: int, total: int) -> None:
        if total > 0:
            pct = downloaded * 100 // total
            console.print(f"  {status} ({pct}%)", end="\r")
        else:
            console.print(f"  {status}", end="\r")

    try:
        result = install_port(
            port=port,
            roms_dir=config.roms_dir,
            ports_dir=config.ports_dir,
            github_token=config.github_token,
            progress_callback=progress_callback,
            install_enhancements=not no_enhancements,
        )

        console.print()  # Clear progress line

        if not result.success:
            console.print(f"[red]Installation failed:[/red]")
            for error in result.errors:
                console.print(f"  [red]{error}[/red]")
            return

        console.print(f"[green]✓ Installed {port.name} {result.version}[/green]")

        for warning in result.warnings:
            console.print(f"  [yellow]Warning: {warning}[/yellow]")

        # Add to Steam
        if not no_steam:
            _add_port_to_steam(port, config)

    except InstallerError as e:
        console.print(f"[red]Installation failed: {e}[/red]")


def _install_rom(game, config: Config, no_steam: bool) -> None:
    """Install a ROM from Myrient."""
    from pier.roms.myrient import MyrientClient, MyrientError, MyrientFile
    from pier.roms.systems import SYSTEMS

    system = SYSTEMS[game.system]
    filename = game.metadata["filename"]
    url = game.metadata["url"]

    console.print(f"\n[bold]Downloading {game.name}...[/bold]")

    # Create MyrientFile from metadata
    myrient_file = MyrientFile(
        name=filename,
        url=url,
        size=game.metadata.get("size", 0),
        date="",
    )

    dest_dir = config.roms_dir / system.id
    dest_dir.mkdir(parents=True, exist_ok=True)

    def progress_callback(downloaded: int, total: int) -> None:
        if total > 0:
            pct = downloaded * 100 // total
            console.print(f"  Downloading... ({pct}%)", end="\r")

    try:
        with MyrientClient() as client:
            rom_path = client.download(
                myrient_file,
                dest_dir,
                progress_callback=progress_callback,
            )

        console.print()  # Clear progress line
        console.print(f"[green]✓ Downloaded to {rom_path}[/green]")

        # Add to Steam
        if not no_steam:
            console.print("\n[dim]Run 'pier sync' to add to Steam.[/dim]")

    except MyrientError as e:
        console.print(f"\n[red]Download failed: {e}[/red]")


def _add_port_to_steam(port, config: Config) -> None:
    """Add an installed port to Steam."""
    from pier.ports.scanner import get_installed_port
    from pier.ports.steam import sync_port_to_steam

    installed = get_installed_port(port.id, config.ports_dir)
    if not installed:
        return

    try:
        sync_port_to_steam(port, installed.install_dir)
        console.print(f"[green]✓ Added to Steam[/green]")

        # Fetch artwork
        if config.steamgriddb_api_key:
            _fetch_artwork_for_port(port, config)

    except Exception as e:
        console.print(f"[yellow]Could not add to Steam: {e}[/yellow]")


def _download_artwork_for_game(sgdb: SteamGridDBClient, sgdb_game_id: int, status) -> int:
    """Download artwork for a game from SteamGridDB.

    Returns count of images downloaded.
    """
    from pier.steam.artwork import ArtworkStatus
    downloaded = 0

    # Map artwork types to getter functions and status flags
    artwork_map = [
        (ArtworkType.POSTER, sgdb.get_grids, status.has_poster),
        (ArtworkType.HERO, sgdb.get_heroes, status.has_hero),
        (ArtworkType.LOGO, sgdb.get_logos, status.has_logo),
        (ArtworkType.ICON, sgdb.get_icons, status.has_icon),
    ]

    for art_type, getter, has_art in artwork_map:
        if has_art:
            continue  # Already have this artwork
        try:
            images = getter(sgdb_game_id)
            if images:
                dest = status.get_dest_path(art_type)
                result = sgdb.download_image(images[0].url, dest)
                if result:
                    downloaded += 1
        except SteamGridDBError:
            pass

    return downloaded


def _fetch_artwork_for_port(port, config: Config) -> None:
    """Fetch artwork for a port."""
    if not config.steamgriddb_api_key:
        return
    try:
        with SteamGridDBClient(config.steamgriddb_api_key) as sgdb:
            search_name = port.steamgriddb_name or port.name
            games = sgdb.search_game(search_name)
            if not games:
                return

            sgdb_game_id = games[0].id

            # Get app ID for the port
            shortcut = find_shortcut(port.name)
            if not shortcut:
                return

            app_id = shortcut.app_id
            if app_id == 0:
                return

            # Download artwork
            from pier.steam.artwork import get_artwork_status
            status = get_artwork_status(app_id)
            if not status:
                return

            _download_artwork_for_game(sgdb, sgdb_game_id, status)
            console.print(f"[green]✓ Fetched artwork[/green]")

    except Exception:
        pass  # Artwork is optional, don't fail the install


# =============================================================================
# Info Command
# =============================================================================


@cli.command()
@click.argument("query")
def info(query: str) -> None:
    """Show details about a game.

    Shows installation status, Steam status, available enhancements, etc.
    """
    config = Config.load()

    # Search for the game
    result = search_games(
        query=query,
        roms_dir=config.roms_dir,
        ports_dir=config.ports_dir,
        include_myrient=True,
        check_updates=True,
        github_token=config.github_token,
    )

    # Find best match
    all_games = result.all_games()
    if not all_games:
        console.print(f"[yellow]No game found matching '{query}'.[/yellow]")
        return

    # Prefer exact match, then installed, then first result
    game = None
    query_lower = query.lower()

    for g in all_games:
        if g.name.lower() == query_lower:
            game = g
            break
        if g.installed and game is None:
            game = g

    if game is None:
        game = all_games[0]

    # Display game info
    _show_game_info(game, config)


def _show_game_info(game, config: Config) -> None:
    """Display detailed game information."""
    console.print(f"\n[bold cyan]{game.name}[/bold cyan]")
    console.print()

    # Basic info
    if game.is_port:
        console.print(f"[bold]Type:[/bold] PC Port")
        console.print(f"[bold]Description:[/bold] {game.description}")
        if game.version:
            console.print(f"[bold]Version:[/bold] {game.version}")
        console.print(f"[bold]GitHub:[/bold] https://github.com/{game.metadata.get('github_repo', 'N/A')}")
    else:
        from pier.roms.systems import SYSTEMS
        system_name = SYSTEMS[game.system].name if game.system in SYSTEMS else game.system
        console.print(f"[bold]Type:[/bold] ROM")
        console.print(f"[bold]System:[/bold] {system_name}")

    console.print()

    # Installation status
    if game.installed:
        console.print("[bold]Status:[/bold] [green]Installed[/green]")
        console.print(f"[bold]In Steam:[/bold] {'[green]Yes[/green]' if game.in_steam else '[yellow]No[/yellow]'}")

        if game.is_port and game.update_available:
            console.print("[bold]Update:[/bold] [yellow]Update available[/yellow]")

        # Show enhancements for ports
        if game.is_port and game.metadata.get("has_enhancements"):
            console.print()
            _show_port_enhancements(game, config)
    else:
        console.print("[bold]Status:[/bold] Not installed")
        if game.source == GameSource.MYRIENT:
            size_mb = game.metadata.get("size", 0) / (1024 * 1024)
            console.print(f"[bold]Download size:[/bold] {size_mb:.1f} MB")

    console.print()
    if not game.installed:
        console.print(f"[dim]Use 'pier install \"{game.name}\"' to install.[/dim]")


def _show_port_enhancements(game, config: Config) -> None:
    """Show enhancement info for a port."""
    from pier.ports.registry import PORTS
    from pier.ports.enhancements import get_installed_enhancements
    from pier.ports.scanner import get_installed_port

    port_id = game.metadata.get("port_id")
    if not port_id or port_id not in PORTS:
        return

    port = PORTS[port_id]
    if not port.enhancements:
        return

    installed = get_installed_port(port_id, config.ports_dir)
    if not installed:
        return

    installed_ids = set(get_installed_enhancements(port, installed.install_dir))

    console.print("[bold]Enhancements:[/bold]")
    for enh in port.enhancements:
        status = "[green]Installed[/green]" if enh.id in installed_ids else "[dim]Not installed[/dim]"
        console.print(f"  {enh.name}: {status}")


# =============================================================================
# Remove Command
# =============================================================================


@cli.command()
@click.argument("query")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@click.option("--keep-steam", is_flag=True, help="Keep Steam shortcut")
def remove(query: str, yes: bool, keep_steam: bool) -> None:
    """Remove an installed game.

    Removes the game files and Steam shortcut.
    """
    config = Config.load()

    # Search for installed games
    result = search_games(
        query=query,
        roms_dir=config.roms_dir,
        ports_dir=config.ports_dir,
        include_myrient=False,
    )

    installed = result.installed_ports + result.installed_roms
    if not installed:
        console.print(f"[yellow]No installed game found matching '{query}'.[/yellow]")
        return

    # Find best match
    game = None
    query_lower = query.lower()
    for g in installed:
        if g.name.lower() == query_lower:
            game = g
            break
    if game is None:
        game = installed[0]

    # Confirm
    if not yes:
        if not click.confirm(f"Remove {game.name}?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    # Remove based on type
    if game.is_port:
        _remove_port(game, config, keep_steam)
    else:
        _remove_rom(game, config, keep_steam)


def _remove_port(game, config: Config, keep_steam: bool) -> None:
    """Remove an installed port."""
    import shutil
    from pier.ports.steam import remove_port_from_steam

    port_id = game.metadata["port_id"]
    port_dir = config.ports_dir / port_id

    # Remove Steam shortcut first
    if not keep_steam and game.in_steam:
        try:
            remove_port_from_steam(port_id)
            console.print(f"[green]✓ Removed from Steam[/green]")
        except Exception as e:
            console.print(f"[yellow]Could not remove from Steam: {e}[/yellow]")

    # Remove files
    if port_dir.exists():
        shutil.rmtree(port_dir)
        console.print(f"[green]✓ Removed {game.name}[/green]")
    else:
        console.print(f"[yellow]Port directory not found: {port_dir}[/yellow]")


def _remove_rom(game, config: Config, keep_steam: bool) -> None:
    """Remove an installed ROM."""
    rom_path = Path(game.metadata["path"])

    # Remove Steam shortcut
    if not keep_steam and game.in_steam:
        try:
            # Find and remove the shortcut by game name
            removed = remove_shortcut(game.name)
            if removed:
                console.print(f"[green]✓ Removed from Steam[/green]")
        except Exception as e:
            console.print(f"[yellow]Could not remove from Steam: {e}[/yellow]")

    # Remove file
    if rom_path.exists():
        rom_path.unlink()
        console.print(f"[green]✓ Removed {game.name}[/green]")
    else:
        console.print(f"[yellow]ROM file not found: {rom_path}[/yellow]")


# =============================================================================
# Update Command
# =============================================================================


@cli.command()
@click.argument("query", required=False)
@click.option("--all", "update_all", is_flag=True, help="Update all ports")
def update(query: str | None, update_all: bool) -> None:
    """Update installed ports.

    Without arguments, shows available updates.
    With QUERY, updates matching port.
    With --all, updates all ports with available updates.
    """
    config = Config.load()

    # Get installed ports with update status
    result = search_games(
        query=query,
        roms_dir=config.roms_dir,
        ports_dir=config.ports_dir,
        include_myrient=False,
        check_updates=True,
        github_token=config.github_token,
    )

    ports_with_updates = [g for g in result.installed_ports if g.update_available]

    if query is None and not update_all:
        # Show update status
        if not ports_with_updates:
            console.print("[green]All ports are up to date.[/green]")
            return

        console.print("[bold]Updates available:[/bold]")
        for game in ports_with_updates:
            console.print(f"  [cyan]{game.name}[/cyan] ({game.version} → latest)")
        console.print()
        console.print("[dim]Use 'pier update --all' to update all, or 'pier update <name>' for specific port.[/dim]")
        return

    # Determine which ports to update
    if update_all:
        to_update = ports_with_updates
    else:
        # Find matching port
        query_lower = query.lower() if query else ""
        to_update = [g for g in result.installed_ports if query_lower in g.name.lower()]
        if not to_update:
            console.print(f"[yellow]No installed port found matching '{query}'.[/yellow]")
            return

    if not to_update:
        console.print("[green]Nothing to update.[/green]")
        return

    # Update each port
    for game in to_update:
        _update_port(game, config)


def _update_port(game, config: Config) -> None:
    """Update a single port."""
    from pier.ports import PORTS, InstallerError, install_port
    from pier.ports.scanner import get_installed_port

    port_id = game.metadata["port_id"]
    port = PORTS[port_id]

    console.print(f"\n[bold]Updating {port.name}...[/bold]")

    def progress_callback(status: str, downloaded: int, total: int) -> None:
        if total > 0:
            pct = downloaded * 100 // total
            console.print(f"  {status} ({pct}%)", end="\r")
        else:
            console.print(f"  {status}", end="\r")

    try:
        result = install_port(
            port=port,
            roms_dir=config.roms_dir,
            ports_dir=config.ports_dir,
            github_token=config.github_token,
            progress_callback=progress_callback,
            install_enhancements=True,
        )

        console.print()  # Clear progress line

        if result.success:
            console.print(f"[green]✓ Updated {port.name} to {result.version}[/green]")
        else:
            console.print(f"[red]Update failed:[/red]")
            for error in result.errors:
                console.print(f"  [red]{error}[/red]")

    except InstallerError as e:
        console.print(f"\n[red]Update failed: {e}[/red]")


# =============================================================================
# Sync Command
# =============================================================================


@cli.command()
@click.option("--dry-run", "--preview", is_flag=True, help="Show what would be synced without making changes")
def sync(dry_run: bool) -> None:
    """Sync all games to Steam.

    Adds installed ROMs and ports to Steam as non-Steam shortcuts.
    Also fetches artwork from SteamGridDB if API key is configured.

    Use --preview to see what would change without making changes.
    """
    config = Config.load()

    if dry_run:
        console.print("[bold]Sync Preview[/bold]")
        console.print()
    elif is_steam_running():
        console.print("[yellow]Warning: Steam is running. Changes may not appear until restart.[/yellow]")
        console.print()

    # Get all installed games
    result = search_games(
        query=None,
        roms_dir=config.roms_dir,
        ports_dir=config.ports_dir,
        include_myrient=False,
    )

    if result.total_installed == 0:
        console.print("[yellow]No games installed to sync.[/yellow]")
        return

    total_added = 0
    total_updated = 0
    total_skipped = 0
    ports_to_add = []

    # Sync ROMs
    if result.installed_roms:
        if not dry_run:
            console.print("[bold]Syncing ROMs to Steam...[/bold]")
        from pier.roms.scanner import scan_roms
        roms = scan_roms(config.roms_dir)

        sync_result = steam_sync_games(roms, dry_run=dry_run)

        if dry_run:
            # Show detailed preview
            for game in sync_result.added:
                console.print(f"  [green]+[/green] {game.display_name} [dim](new)[/dim]")
            for game in sync_result.adopted:
                console.print(f"  [cyan]~[/cyan] {game.display_name} [dim](adopt existing)[/dim]")
            for game in sync_result.updated:
                console.print(f"  [yellow]~[/yellow] {game.display_name} [dim](update)[/dim]")
            for game in sync_result.skipped_external:
                console.print(f"  [dim]-[/dim] {game.display_name} [dim](external shortcut)[/dim]")

            total_added += len(sync_result.added)
            total_updated += len(sync_result.updated) + len(sync_result.adopted)
            total_skipped += len(sync_result.skipped_external)
        else:
            if sync_result.added:
                console.print(f"[green]Added {len(sync_result.added)} games[/green]")
            if sync_result.adopted:
                console.print(f"[cyan]Adopted {len(sync_result.adopted)} existing shortcuts[/cyan]")
            if sync_result.updated:
                console.print(f"[yellow]Updated {len(sync_result.updated)} games[/yellow]")
            if sync_result.skipped_external:
                console.print(f"[dim]Skipped {len(sync_result.skipped_external)} (external shortcuts)[/dim]")
            if not sync_result.added and not sync_result.adopted and not sync_result.updated and not sync_result.skipped_external:
                console.print("[dim]All ROMs already synced.[/dim]")
            console.print()

    # Sync ports
    if result.installed_ports:
        if not dry_run:
            console.print("[bold]Syncing ports to Steam...[/bold]")
        from pier.ports.steam import sync_port_to_steam

        ports_added = 0
        for game in result.installed_ports:
            if game.in_steam:
                continue

            port_id = game.metadata["port_id"]
            from pier.ports.registry import PORTS
            from pier.ports.scanner import get_installed_port

            port = PORTS[port_id]
            installed = get_installed_port(port_id, config.ports_dir)

            if installed:
                if dry_run:
                    console.print(f"  [green]+[/green] {port.name} [dim](port)[/dim]")
                    ports_to_add.append(port.name)
                    total_added += 1
                else:
                    try:
                        sync_port_to_steam(port, installed.install_dir)
                        ports_added += 1
                    except Exception as e:
                        console.print(f"[yellow]Could not add {port.name}: {e}[/yellow]")

        if not dry_run:
            if ports_added > 0:
                console.print(f"[green]Added {ports_added} ports to Steam[/green]")
            else:
                console.print("[dim]All ports already synced.[/dim]")
            console.print()

    # Fetch artwork
    if config.steamgriddb_api_key and not dry_run:
        console.print("[bold]Fetching artwork...[/bold]")
        _fetch_all_artwork(config)

    if dry_run:
        console.print()
        if total_added == 0 and total_updated == 0 and total_skipped == 0:
            console.print("[green]Everything is already synced.[/green]")
        else:
            summary = []
            if total_added > 0:
                summary.append(f"add {total_added}")
            if total_updated > 0:
                summary.append(f"update {total_updated}")
            if total_skipped > 0:
                summary.append(f"skip {total_skipped}")
            console.print(f"Would {', '.join(summary)}.")
        console.print()
        console.print("[dim]Run 'pier sync' to apply changes.[/dim]")
    else:
        console.print("[green]✓ Sync complete[/green]")


def _fetch_all_artwork(config: Config) -> None:
    """Fetch artwork for all shortcuts missing it."""
    from pier.steam.artwork import get_artwork_status

    shortcuts = get_all_shortcuts()
    if not shortcuts:
        return

    if not config.steamgriddb_api_key:
        return

    try:
        with SteamGridDBClient(config.steamgriddb_api_key) as sgdb:
            fetched = 0
            for shortcut in shortcuts:
                app_id = shortcut.app_id
                if app_id == 0:
                    continue

                # Check if missing artwork
                status = get_artwork_status(app_id)
                if not status:
                    continue
                if status.has_poster and status.has_hero:
                    continue

                # Search and download
                name = shortcut.name
                try:
                    games = sgdb.search_game(name)
                    if not games:
                        continue

                    sgdb_game_id = games[0].id
                    downloaded = _download_artwork_for_game(sgdb, sgdb_game_id, status)
                    if downloaded > 0:
                        fetched += 1
                except Exception:
                    pass

            if fetched > 0:
                console.print(f"[green]Fetched artwork for {fetched} games[/green]")

    except Exception as e:
        console.print(f"[yellow]Could not fetch artwork: {e}[/yellow]")


# =============================================================================
# Steam Group
# =============================================================================


@cli.group(invoke_without_command=True)
@click.pass_context
def steam(ctx: click.Context) -> None:
    """Steam integration commands.

    Run without subcommand to see Steam status.
    """
    if ctx.invoked_subcommand is None:
        _show_steam_status()


def _show_steam_status() -> None:
    """Show Steam integration status."""
    running = is_steam_running()
    status = "[green]Running[/green]" if running else "[yellow]Not running[/yellow]"
    console.print(f"[bold]Steam:[/bold] {status}")

    shortcuts = get_all_shortcuts()
    pier_shortcuts = [s for s in shortcuts if s.is_pier]

    console.print(f"[bold]Total shortcuts:[/bold] {len(shortcuts)}")
    console.print(f"[bold]Pier-managed:[/bold] {len(pier_shortcuts)}")
    console.print()
    console.print("[dim]Use 'pier steam list' to see all shortcuts.[/dim]")


@steam.command("list")
def steam_list() -> None:
    """List pier-managed shortcuts and their status."""
    shortcuts = get_all_shortcuts()
    pier_shortcuts = [s for s in shortcuts if s.is_pier]

    if not pier_shortcuts:
        console.print("[yellow]No pier-managed shortcuts found.[/yellow]")
        console.print("[dim]Use 'pier sync' to add games to Steam.[/dim]")
        return

    # Load config to get paths for status checking
    config = Config.load()

    table = Table(title=f"Pier Games in Steam ({len(pier_shortcuts)})")
    table.add_column("Name", style="cyan")
    table.add_column("Status")
    table.add_column("Artwork", justify="center")

    broken_count = 0
    needs_sync_count = 0

    for shortcut in pier_shortcuts:
        name = shortcut.name
        status, status_display = _get_shortcut_status_display(shortcut, config)

        if status == ShortcutStatus.BROKEN:
            broken_count += 1
        elif status == ShortcutStatus.NEEDS_SYNC:
            needs_sync_count += 1

        # Check artwork
        app_id = shortcut.app_id
        if app_id:
            from pier.steam.artwork import get_artwork_status
            art = get_artwork_status(app_id)
            has_art = art and (art.has_poster or art.has_hero)
            art_status = "[green]✓[/green]" if has_art else "[dim]-[/dim]"
        else:
            art_status = "[dim]?[/dim]"

        table.add_row(name, status_display, art_status)

    console.print(table)

    # Show summary of issues
    if broken_count > 0 or needs_sync_count > 0:
        console.print()
        if broken_count > 0:
            console.print(f"[yellow]{broken_count} broken shortcut(s) (files missing)[/yellow]")
        if needs_sync_count > 0:
            console.print(f"[yellow]{needs_sync_count} shortcut(s) need resync[/yellow]")
        console.print("[dim]Run 'pier sync' to fix, or 'pier steam remove <name>' to remove broken shortcuts.[/dim]")


def _get_shortcut_status_display(shortcut, config: Config) -> tuple[str, str]:
    """Get status and display string for a shortcut.

    Returns:
        Tuple of (status_code, display_string).
    """
    from pathlib import Path

    # Parse DevkitGameID to determine type
    game_id = None
    for sc in get_all_shortcuts():
        if sc.index == shortcut.index:
            # Need to get the raw game ID - look it up from VDF
            break

    # Get the game ID from VDF
    from pier.steam.vdf import load_shortcuts
    data = load_shortcuts()
    shortcuts_data = data.get("shortcuts", {})
    game_id = None
    for key, entry in shortcuts_data.items():
        if isinstance(entry, dict) and entry.get("AppName") == shortcut.name:
            game_id = entry.get("DevkitGameID", "")
            break

    if not game_id:
        # No game ID - can't determine status
        return ShortcutStatus.READY, "[green]✓ Ready[/green]"

    # Check if it's a port or ROM
    if game_id.startswith("port:"):
        return _get_port_shortcut_status(shortcut, game_id[5:], config)
    else:
        return _get_rom_shortcut_status(shortcut, game_id, config)


def _get_port_shortcut_status(shortcut, port_id: str, config: Config) -> tuple[str, str]:
    """Get status for a port shortcut."""
    from pier.ports.registry import PORTS
    from pier.ports.scanner import get_installed_port
    from pier.ports.steam import get_expected_port_values
    from pier.ports.updater import check_for_update

    if port_id not in PORTS:
        return ShortcutStatus.BROKEN, "[red]⚠ Broken[/red]"

    port = PORTS[port_id]
    installed = get_installed_port(port_id, config.ports_dir)

    if not installed:
        return ShortcutStatus.BROKEN, "[red]⚠ Broken[/red] (not installed)"

    # Get expected values and check staleness
    expected_exe, expected_start_dir, expected_launch_options = get_expected_port_values(
        port, installed.install_dir
    )

    status = get_shortcut_status(
        shortcut,
        file_exists=True,  # We already confirmed installed
        expected_exe=expected_exe,
        expected_start_dir=expected_start_dir,
        expected_launch_options=expected_launch_options,
        update_available=False,
    )

    # Check for update separately (if requested)
    if status == ShortcutStatus.READY:
        try:
            if check_for_update(installed, config.github_token):
                return ShortcutStatus.UPDATE_AVAILABLE, "[cyan]↑ Update available[/cyan]"
        except Exception:
            pass  # Ignore update check errors

    if status == ShortcutStatus.NEEDS_SYNC:
        return status, "[yellow]⚠ Needs sync[/yellow]"

    return ShortcutStatus.READY, "[green]✓ Ready[/green]"


def _get_rom_shortcut_status(shortcut, game_id: str, config: Config) -> tuple[str, str]:
    """Get status for a ROM shortcut."""
    from pathlib import Path
    from pier.roms.scanner import scan_roms
    from pier.steam.sync import get_expected_rom_values

    # Find the ROM by ID
    roms = scan_roms(config.roms_dir)
    rom = None
    for r in roms:
        if r.id == game_id:
            rom = r
            break

    if not rom:
        return ShortcutStatus.BROKEN, "[red]⚠ Broken[/red] (ROM missing)"

    # Check if file exists
    if not rom.path.exists():
        return ShortcutStatus.BROKEN, "[red]⚠ Broken[/red] (file missing)"

    # Get expected values and check staleness
    expected_exe, expected_start_dir, expected_launch_options = get_expected_rom_values(rom)

    status = get_shortcut_status(
        shortcut,
        file_exists=True,
        expected_exe=expected_exe,
        expected_start_dir=expected_start_dir,
        expected_launch_options=expected_launch_options,
        update_available=False,
    )

    if status == ShortcutStatus.NEEDS_SYNC:
        return status, "[yellow]⚠ Needs sync[/yellow]"

    return ShortcutStatus.READY, "[green]✓ Ready[/green]"


@steam.command("artwork")
@click.argument("query", required=False)
@click.option("--all", "fetch_all", is_flag=True, help="Fetch for all shortcuts")
def steam_artwork(query: str | None, fetch_all: bool) -> None:
    """Fetch artwork from SteamGridDB.

    Without arguments, shows artwork status.
    With QUERY, fetches artwork for matching shortcut.
    With --all, fetches for all shortcuts missing artwork.
    """
    config = Config.load()

    if not config.steamgriddb_api_key:
        console.print("[yellow]SteamGridDB API key not configured.[/yellow]")
        console.print("[dim]Run 'pier config steamgriddb_api_key <key>' to set it.[/dim]")
        return

    shortcuts = get_all_shortcuts()
    if not shortcuts:
        console.print("[yellow]No shortcuts found.[/yellow]")
        return

    if query is None and not fetch_all:
        # Show artwork status
        _show_artwork_status(shortcuts)
        return

    # Determine which shortcuts to process
    if fetch_all:
        to_process = shortcuts
    elif query:  # query is guaranteed non-None here since we returned early above
        shortcut = find_shortcut(query)
        if not shortcut:
            console.print(f"[yellow]No shortcut found matching '{query}'.[/yellow]")
            return
        to_process = [shortcut]
    else:
        return  # Should never reach here

    # Fetch artwork
    _fetch_artwork_for_shortcuts(to_process, config)


def _show_artwork_status(shortcuts: list) -> None:
    """Show artwork status for all shortcuts."""
    from pier.steam.artwork import get_artwork_status

    missing = []
    for shortcut in shortcuts:
        app_id = shortcut.app_id
        if app_id == 0:
            continue

        status = get_artwork_status(app_id)
        if not status or not status.has_poster or not status.has_hero:
            missing.append(shortcut.name)

    if not missing:
        console.print("[green]All shortcuts have artwork.[/green]")
    else:
        console.print(f"[yellow]{len(missing)} shortcut(s) missing artwork:[/yellow]")
        for name in missing[:10]:
            console.print(f"  {name}")
        if len(missing) > 10:
            console.print(f"  ... and {len(missing) - 10} more")
        console.print()
        console.print("[dim]Use 'pier steam artwork --all' to fetch missing artwork.[/dim]")


def _fetch_artwork_for_shortcuts(shortcuts: list, config: Config) -> None:
    """Fetch artwork for specified shortcuts."""
    from pier.steam.artwork import get_artwork_status

    if not config.steamgriddb_api_key:
        return

    try:
        with SteamGridDBClient(config.steamgriddb_api_key) as sgdb:
            for shortcut in shortcuts:
                name = shortcut.name
                app_id = shortcut.app_id
                if not name or app_id == 0:
                    continue

                console.print(f"Fetching artwork for {name}...", end=" ")

                try:
                    games = sgdb.search_game(name)
                    if not games:
                        console.print("[yellow]not found[/yellow]")
                        continue

                    sgdb_game_id = games[0].id
                    status = get_artwork_status(app_id)
                    if not status:
                        console.print("[yellow]no grid dir[/yellow]")
                        continue

                    fetched = _download_artwork_for_game(sgdb, sgdb_game_id, status)

                    if fetched > 0:
                        console.print(f"[green]✓ ({fetched} images)[/green]")
                    else:
                        console.print("[yellow]no artwork available[/yellow]")

                except Exception as e:
                    console.print(f"[red]error: {e}[/red]")

    except Exception as e:
        console.print(f"[red]Failed to connect to SteamGridDB: {e}[/red]")


@steam.command("remove")
@click.argument("query")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def steam_remove(query: str, yes: bool) -> None:
    """Remove a Steam shortcut.

    Only removes the shortcut, not the game files.
    """
    shortcut = find_shortcut(query)

    if not shortcut:
        console.print(f"[yellow]No shortcut found matching '{query}'.[/yellow]")
        return

    name = shortcut.name

    if not yes:
        if not click.confirm(f"Remove shortcut for '{name}'?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    try:
        remove_shortcut(query)
        console.print(f"[green]✓ Removed shortcut for '{name}'[/green]")
    except Exception as e:
        console.print(f"[red]Failed to remove shortcut: {e}[/red]")


# =============================================================================
# Config Command
# =============================================================================


@cli.command()
@click.argument("key", required=False)
@click.argument("value", required=False)
def config(key: str | None, value: str | None) -> None:
    """View or set configuration.

    Without arguments, shows all config.
    With KEY, shows that config value.
    With KEY VALUE, sets the config value.
    """
    cfg = Config.load()

    if key is None:
        # Show all config
        console.print("[bold]Configuration:[/bold]")
        console.print(f"  roms_dir: {cfg.roms_dir}")
        console.print(f"  ports_dir: {cfg.ports_dir}")
        console.print(f"  steamgriddb_api_key: {'[set]' if cfg.steamgriddb_api_key else '[not set]'}")
        console.print(f"  github_token: {'[set]' if cfg.github_token else '[not set]'}")
        console.print()
        console.print(f"[dim]Config file: {cfg.config_path}[/dim]")
        return

    # Valid config keys
    valid_keys = ["roms_dir", "ports_dir", "steamgriddb_api_key", "github_token"]

    if key not in valid_keys:
        console.print(f"[red]Unknown config key: {key}[/red]")
        console.print(f"Valid keys: {', '.join(valid_keys)}")
        return

    if value is None:
        # Show specific config
        val = getattr(cfg, key)
        if key in ["steamgriddb_api_key", "github_token"]:
            val = "[set]" if val else "[not set]"
        console.print(f"{key}: {val}")
        return

    # Set config
    if key == "roms_dir":
        cfg.roms_dir = Path(value).expanduser()
    elif key == "ports_dir":
        cfg.ports_dir = Path(value).expanduser()
    elif key == "steamgriddb_api_key":
        cfg.steamgriddb_api_key = value if value != "" else None
    elif key == "github_token":
        cfg.github_token = value if value != "" else None

    cfg.save()
    console.print(f"[green]✓ Set {key}[/green]")


# =============================================================================
# Entry Point
# =============================================================================


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
