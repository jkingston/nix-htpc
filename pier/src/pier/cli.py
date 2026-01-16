"""Pier CLI - ROM management for NixOS HTPC."""

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from pier import __version__
from pier.config import Config
from pier.roms.scanner import scan_roms
from pier.roms.systems import SYSTEMS
from pier.steam.artwork import ArtworkType
from pier.steam.paths import find_shortcuts_vdf, find_steam_userdata, is_steam_running
from pier.steam.shortcuts import (
    find_shortcut,
    get_all_shortcuts,
    get_shortcut_details,
    remove_shortcut,
)
from pier.steam.steamgriddb import SteamGridDBClient, SteamGridDBError
from pier.steam.sync import get_pier_shortcuts, sync_games
from pier.steam.vdf import load_shortcuts

console = Console()


@click.group()
@click.version_option(version=__version__)
def cli() -> None:
    """Pier - ROM management for NixOS HTPC."""
    pass


@cli.command("list")
@click.argument("system", required=False)
@click.option("--in-steam", is_flag=True, help="Only show games synced to Steam")
@click.option("--not-in-steam", is_flag=True, help="Only show games not synced to Steam")
def list_games(system: str | None, in_steam: bool, not_in_steam: bool) -> None:
    """List ROMs on disk.

    Optionally filter by SYSTEM (e.g., n64, snes, ps2).
    """
    config = Config.load()

    if system and system not in SYSTEMS:
        console.print(f"[red]Unknown system: {system}[/red]")
        console.print(f"Available systems: {', '.join(SYSTEMS.keys())}")
        raise SystemExit(1)

    games = scan_roms(config.roms_dir, system_filter=system)

    if not games:
        console.print("[yellow]No ROMs found.[/yellow]")
        console.print(f"ROM directory: {config.roms_dir}")
        return

    # Check which games are in Steam
    shortcuts_data = load_shortcuts()
    pier_shortcuts = get_pier_shortcuts(shortcuts_data)
    steam_game_ids = set(pier_shortcuts.keys())

    for game in games:
        game.in_steam = game.id in steam_game_ids

    # Apply filters
    if in_steam:
        games = [g for g in games if g.in_steam]
    elif not_in_steam:
        games = [g for g in games if not g.in_steam]

    if not games:
        console.print("[yellow]No matching games found.[/yellow]")
        return

    # Group by system
    by_system: dict[str, list] = {}
    for game in games:
        by_system.setdefault(game.system.id, []).append(game)

    for sys_id in sorted(by_system.keys()):
        sys_games = by_system[sys_id]
        system_info = SYSTEMS[sys_id]

        table = Table(title=f"{system_info.name} ({len(sys_games)} ROMs)")
        table.add_column("Name", style="cyan")
        table.add_column("Steam", justify="center")
        table.add_column("File", style="dim")

        for game in sys_games:
            steam_status = "[green]Yes[/green]" if game.in_steam else "[dim]No[/dim]"
            table.add_row(game.display_name, steam_status, game.filename)

        console.print(table)
        console.print()


@cli.command()
@click.argument("system", required=False)
@click.option("--dry-run", is_flag=True, help="Show what would be synced without making changes")
def sync(system: str | None, dry_run: bool) -> None:
    """Sync ROMs to Steam shortcuts.

    Optionally filter by SYSTEM (e.g., n64, snes, ps2).
    """
    config = Config.load()

    # Check Steam is available
    if not find_steam_userdata():
        console.print("[red]Could not find Steam userdata directory.[/red]")
        console.print("Is Steam installed?")
        raise SystemExit(1)

    if system and system not in SYSTEMS:
        console.print(f"[red]Unknown system: {system}[/red]")
        raise SystemExit(1)

    games = scan_roms(config.roms_dir, system_filter=system)

    if not games:
        console.print("[yellow]No ROMs found to sync.[/yellow]")
        return

    result = sync_games(games, dry_run=dry_run)

    if dry_run:
        console.print("[bold]Dry run - no changes made[/bold]\n")

    if result.added:
        console.print(f"[green]Added {len(result.added)} games:[/green]")
        for game in result.added:
            console.print(f"  + {game.display_name} ({game.system.name})")

    if result.adopted:
        console.print(f"[cyan]Adopted {len(result.adopted)} existing shortcuts:[/cyan]")
        for game in result.adopted:
            console.print(f"  ~ {game.display_name} ({game.system.name})")

    if result.updated:
        console.print(f"[yellow]Updated {len(result.updated)} games:[/yellow]")
        for game in result.updated:
            console.print(f"  ~ {game.display_name} ({game.system.name})")

    if result.removed:
        console.print(f"[red]Removed {len(result.removed)} shortcuts:[/red]")
        for game_id in result.removed:
            console.print(f"  - {game_id}")

    if result.unchanged:
        console.print(f"[dim]Unchanged: {len(result.unchanged)} games[/dim]")

    total_changes = len(result.added) + len(result.adopted) + len(result.updated) + len(result.removed)
    if total_changes > 0 and not dry_run:
        if is_steam_running():
            console.print("\n[yellow]Note: Steam is running. Restart Steam to see changes.[/yellow]")
        else:
            console.print("\n[dim]Changes will appear when Steam starts.[/dim]")
    elif total_changes == 0:
        console.print("[green]Everything up to date.[/green]")


@cli.command()
@click.argument("key", required=False)
@click.argument("value", required=False)
def config(key: str | None, value: str | None) -> None:
    """View or set configuration.

    With no arguments, shows all config values.
    With KEY, shows that config value.
    With KEY and VALUE, sets the config value.
    """
    cfg = Config.load()

    if key is None:
        # Show all config
        table = Table(title="Configuration")
        table.add_column("Key", style="cyan")
        table.add_column("Value")

        table.add_row("roms_dir", str(cfg.roms_dir))
        table.add_row(
            "steamgriddb_api_key",
            cfg.steamgriddb_api_key if cfg.steamgriddb_api_key else "[dim]not set[/dim]"
        )

        console.print(table)
        return

    if value is None:
        # Show specific config value
        val = cfg.get(key)
        if val is None:
            console.print(f"[red]Unknown config key: {key}[/red]")
            raise SystemExit(1)
        console.print(val)
        return

    # Set config value
    if not cfg.set(key, value):
        console.print(f"[red]Unknown config key: {key}[/red]")
        raise SystemExit(1)

    cfg.save()
    console.print(f"[green]Set {key} = {value}[/green]")


@cli.command()
def systems() -> None:
    """List supported ROM systems."""
    table = Table(title="Supported Systems")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Extensions", style="dim")
    table.add_column("Emulator")

    for sys_id, system in sorted(SYSTEMS.items()):
        exts = ", ".join(sorted(system.extensions))
        table.add_row(sys_id, system.name, exts, system.emulator.split()[0])

    console.print(table)


@cli.command()
def status() -> None:
    """Show status of pier and Steam integration."""
    config = Config.load()

    console.print("[bold]Pier Status[/bold]\n")

    # Config
    console.print(f"ROM directory: {config.roms_dir}")
    if config.roms_dir.exists():
        console.print("  [green]exists[/green]")
    else:
        console.print("  [red]does not exist[/red]")

    # Steam
    console.print()
    userdata = find_steam_userdata()
    if userdata:
        console.print(f"Steam userdata: {userdata}")
        shortcuts_path = find_shortcuts_vdf()
        if shortcuts_path and shortcuts_path.exists():
            all_shortcuts = get_all_shortcuts()
            pier_count = sum(1 for s in all_shortcuts if s.is_pier)
            console.print(f"  Non-Steam shortcuts: {len(all_shortcuts)} ({pier_count} pier-tagged)")
        else:
            console.print("  [yellow]No shortcuts.vdf found[/yellow]")
    else:
        console.print("[red]Steam not found[/red]")

    # ROMs on disk
    console.print()
    games = scan_roms(config.roms_dir)
    console.print(f"ROMs on disk: {len(games)}")

    if games:
        by_system: dict[str, int] = {}
        for game in games:
            by_system[game.system.id] = by_system.get(game.system.id, 0) + 1
        for sys_id, count in sorted(by_system.items()):
            console.print(f"  {SYSTEMS[sys_id].name}: {count}")


# --- Steam command group ---


@cli.group(invoke_without_command=True)
@click.pass_context
def steam(ctx: click.Context) -> None:
    """Manage Steam shortcuts.

    Without a subcommand, lists all non-Steam shortcuts.
    """
    if ctx.invoked_subcommand is None:
        # Default to listing shortcuts
        ctx.invoke(steam_list)


@steam.command("list")
def steam_list() -> None:
    """List all non-Steam shortcuts."""
    userdata = find_steam_userdata()
    if not userdata:
        console.print("[red]Steam not found[/red]")
        raise SystemExit(1)

    all_shortcuts = get_all_shortcuts()

    if not all_shortcuts:
        console.print("[yellow]No non-Steam shortcuts found.[/yellow]")
        return

    table = Table(title=f"Steam Shortcuts ({len(all_shortcuts)})")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Name", style="cyan")
    table.add_column("Pier", justify="center")
    table.add_column("Poster", justify="center")
    table.add_column("Hero", justify="center")
    table.add_column("Logo", justify="center")
    table.add_column("Icon", justify="center")

    def _check(has: bool) -> str:
        return "[green]\u2713[/green]" if has else "[dim]-[/dim]"

    missing_count = 0
    for shortcut in all_shortcuts:
        artwork = shortcut.artwork
        if artwork:
            has_all = artwork.complete
            if not has_all:
                missing_count += 1
            table.add_row(
                shortcut.index,
                shortcut.name,
                _check(shortcut.is_pier),
                _check(artwork.has_poster),
                _check(artwork.has_hero),
                _check(artwork.has_logo),
                _check(artwork.has_icon),
            )
        else:
            missing_count += 1
            table.add_row(
                shortcut.index,
                shortcut.name,
                _check(shortcut.is_pier),
                "[dim]-[/dim]",
                "[dim]-[/dim]",
                "[dim]-[/dim]",
                "[dim]-[/dim]",
            )

    console.print(table)

    if missing_count > 0:
        console.print(f"\n[yellow]Missing artwork: {missing_count} shortcut(s)[/yellow]")
        console.print("[dim]Run 'pier steam fetch-artwork' to download[/dim]")


@steam.command("info")
@click.argument("query")
def steam_info(query: str) -> None:
    """Show details of a shortcut.

    QUERY can be an index number or partial name match.
    """
    shortcut = find_shortcut(query)
    if not shortcut:
        console.print(f"[red]No shortcut found matching: {query}[/red]")
        raise SystemExit(1)

    details = get_shortcut_details(shortcut)

    table = Table(title=f"Shortcut: {shortcut.name}")
    table.add_column("Property", style="cyan")
    table.add_column("Value")

    for key, value in details.items():
        table.add_row(key, value)

    console.print(table)


@steam.command("remove")
@click.argument("query")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def steam_remove(query: str, yes: bool) -> None:
    """Remove a shortcut from Steam.

    QUERY can be an index number or partial name match.
    """
    shortcut = find_shortcut(query)
    if not shortcut:
        console.print(f"[red]No shortcut found matching: {query}[/red]")
        raise SystemExit(1)

    if not yes:
        console.print(f"Remove shortcut: [cyan]{shortcut.name}[/cyan]?")
        if not click.confirm("Proceed?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    removed = remove_shortcut(query)
    if removed:
        console.print(f"[green]Removed: {removed.name}[/green]")
        if is_steam_running():
            console.print("\n[yellow]Note: Steam is running. Restart Steam to see changes.[/yellow]")
    else:
        console.print("[red]Failed to remove shortcut.[/red]")
        raise SystemExit(1)


@steam.command("fetch-artwork")
@click.argument("query", required=False)
@click.option("--all", "fetch_all", is_flag=True, help="Fetch artwork for all shortcuts")
def steam_fetch_artwork(query: str | None, fetch_all: bool) -> None:
    """Fetch artwork from SteamGridDB.

    QUERY can be an index number or partial name match.
    Use --all to fetch for all shortcuts.
    """
    config = Config.load()

    if not config.steamgriddb_api_key:
        console.print("[red]SteamGridDB API key not configured.[/red]")
        console.print()
        console.print("Get a free API key at:")
        console.print("  [cyan]https://www.steamgriddb.com/profile/preferences/api[/cyan]")
        console.print()
        console.print("Then set it with:")
        console.print("  [cyan]pier config steamgriddb_api_key YOUR_KEY[/cyan]")
        raise SystemExit(1)

    if not query and not fetch_all:
        console.print("[red]Specify a shortcut or use --all[/red]")
        raise SystemExit(1)

    # Get shortcuts to process
    if fetch_all:
        shortcuts_to_fetch = get_all_shortcuts()
    else:
        assert query is not None  # Checked above
        shortcut = find_shortcut(query)
        if not shortcut:
            console.print(f"[red]No shortcut found matching: {query}[/red]")
            raise SystemExit(1)
        shortcuts_to_fetch = [shortcut]

    if not shortcuts_to_fetch:
        console.print("[yellow]No shortcuts to process.[/yellow]")
        return

    try:
        client = SteamGridDBClient(config.steamgriddb_api_key)

        for shortcut in shortcuts_to_fetch:
            console.print(f"\n[bold]Fetching artwork for: {shortcut.name}[/bold]")

            # Search for game on SteamGridDB
            console.print("  Searching SteamGridDB...", end=" ")
            try:
                games = client.search_game(shortcut.name)
            except SteamGridDBError as e:
                console.print(f"[red]error: {e}[/red]")
                continue

            if not games:
                console.print("[yellow]not found[/yellow]")
                continue

            game = games[0]  # Use best match
            console.print(f"[green]found: \"{game.name}\"[/green]")

            # Get artwork paths
            artwork = shortcut.artwork
            if not artwork:
                console.print("  [red]Could not determine artwork paths[/red]")
                continue

            downloaded = 0

            # Fetch poster/grid
            if not artwork.has_poster:
                console.print("  Downloading poster...", end=" ")
                try:
                    grids = client.get_grids(game.id)
                    if grids:
                        dest = artwork.get_dest_path(ArtworkType.POSTER)
                        if client.download_image(grids[0].url, dest):
                            console.print("[green]\u2713[/green]")
                            downloaded += 1
                        else:
                            console.print("[red]failed[/red]")
                    else:
                        console.print("[yellow]none available[/yellow]")
                except SteamGridDBError:
                    console.print("[red]error[/red]")

            # Fetch hero
            if not artwork.has_hero:
                console.print("  Downloading hero...", end=" ")
                try:
                    heroes = client.get_heroes(game.id)
                    if heroes:
                        dest = artwork.get_dest_path(ArtworkType.HERO)
                        if client.download_image(heroes[0].url, dest):
                            console.print("[green]\u2713[/green]")
                            downloaded += 1
                        else:
                            console.print("[red]failed[/red]")
                    else:
                        console.print("[yellow]none available[/yellow]")
                except SteamGridDBError:
                    console.print("[red]error[/red]")

            # Fetch logo
            if not artwork.has_logo:
                console.print("  Downloading logo...", end=" ")
                try:
                    logos = client.get_logos(game.id)
                    if logos:
                        dest = artwork.get_dest_path(ArtworkType.LOGO)
                        if client.download_image(logos[0].url, dest):
                            console.print("[green]\u2713[/green]")
                            downloaded += 1
                        else:
                            console.print("[red]failed[/red]")
                    else:
                        console.print("[yellow]none available[/yellow]")
                except SteamGridDBError:
                    console.print("[red]error[/red]")

            # Fetch icon
            if not artwork.has_icon:
                console.print("  Downloading icon...", end=" ")
                try:
                    icons = client.get_icons(game.id)
                    if icons:
                        dest = artwork.get_dest_path(ArtworkType.ICON)
                        if client.download_image(icons[0].url, dest):
                            console.print("[green]\u2713[/green]")
                            downloaded += 1
                        else:
                            console.print("[red]failed[/red]")
                    else:
                        console.print("[yellow]none available[/yellow]")
                except SteamGridDBError:
                    console.print("[red]error[/red]")

            if downloaded > 0:
                console.print(f"  [green]Downloaded {downloaded} artwork(s)[/green]")
            else:
                console.print("  [dim]No new artwork downloaded[/dim]")

        client.close()

    except SteamGridDBError as e:
        console.print(f"[red]SteamGridDB error: {e}[/red]")
        raise SystemExit(1) from None


# --- ROM Downloads commands ---


@cli.group()
def roms() -> None:
    """Search and download ROMs from Myrient."""
    pass


@roms.command("search")
@click.argument("query")
@click.option("--system", "-s", help="Filter by system (e.g., n64, snes, ps2)")
@click.option("--limit", "-n", default=10, help="Maximum results per system")
def roms_search(query: str, system: str | None, limit: int) -> None:
    """Search Myrient for ROMs.

    Searches the No-Intro and Redump archives for matching ROM files.
    """
    from pier.roms.myrient import MyrientClient, MyrientError
    from pier.roms.systems import SYSTEMS

    if system and system not in SYSTEMS:
        console.print(f"[red]Unknown system: {system}[/red]")
        console.print(f"Available systems: {', '.join(SYSTEMS.keys())}")
        raise SystemExit(1)

    # Determine which systems to search
    systems_to_search = [SYSTEMS[system]] if system else list(SYSTEMS.values())

    console.print(f"Searching Myrient for: [cyan]{query}[/cyan]")
    console.print()

    try:
        with MyrientClient() as client:
            total_found = 0

            for sys in systems_to_search:
                if not sys.myrient_path:
                    continue

                try:
                    matches = client.search(sys, query)
                except MyrientError:
                    continue

                if not matches:
                    continue

                total_found += len(matches)

                table = Table(title=f"{sys.name}")
                table.add_column("Name", style="cyan")
                table.add_column("Size", justify="right")

                for file in matches[:limit]:
                    size_mb = file.size / (1024 * 1024)
                    size_str = f"{size_mb:.1f} MB" if size_mb >= 1 else f"{file.size / 1024:.0f} KB"
                    table.add_row(file.name, size_str)

                console.print(table)

                if len(matches) > limit:
                    console.print(f"  [dim]... and {len(matches) - limit} more[/dim]")
                console.print()

            if total_found == 0:
                console.print("[yellow]No ROMs found matching your query.[/yellow]")

    except MyrientError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from None


@roms.command("download")
@click.argument("query")
@click.option("--system", "-s", required=True, help="System to download from (e.g., n64, snes)")
@click.option("--exact", is_flag=True, help="Require exact name match")
def roms_download(query: str, system: str, exact: bool) -> None:
    """Download a ROM from Myrient.

    Downloads the ROM to your configured roms_dir.
    """
    from pier.roms.myrient import MyrientClient, MyrientError
    from pier.roms.systems import SYSTEMS

    if system not in SYSTEMS:
        console.print(f"[red]Unknown system: {system}[/red]")
        console.print(f"Available systems: {', '.join(SYSTEMS.keys())}")
        raise SystemExit(1)

    config = Config.load()
    sys_info = SYSTEMS[system]

    if not sys_info.myrient_path:
        console.print(f"[red]System {system} does not have Myrient path configured.[/red]")
        raise SystemExit(1)

    console.print(f"Searching for: [cyan]{query}[/cyan] in {sys_info.name}")

    try:
        with MyrientClient() as client:
            matches = client.search(sys_info, query)

            if not matches:
                console.print("[red]No matching ROMs found.[/red]")
                raise SystemExit(1)

            # If exact match required, filter
            if exact:
                query_lower = query.lower()
                matches = [m for m in matches if Path(m.name).stem.lower() == query_lower]
                if not matches:
                    console.print("[red]No exact match found.[/red]")
                    raise SystemExit(1)

            # Use first match
            file = matches[0]
            console.print(f"Found: [green]{file.name}[/green]")

            # Download to system directory
            dest_dir = config.roms_dir / system
            dest_dir.mkdir(parents=True, exist_ok=True)

            console.print(f"Downloading to: {dest_dir}")
            console.print()

            def progress_callback(downloaded: int, total: int) -> None:
                if total > 0:
                    pct = downloaded * 100 // total
                    bar_len = 40
                    filled = bar_len * downloaded // total
                    bar = "█" * filled + "░" * (bar_len - filled)
                    console.print(f"  [{bar}] {pct}%", end="\r")

            rom_path = client.download(file, dest_dir, progress_callback=progress_callback)
            console.print()
            console.print(f"[green]✓ Downloaded: {rom_path.name}[/green]")

    except MyrientError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from None


@roms.command("verify")
@click.argument("system", required=False)
def roms_verify(system: str | None) -> None:
    """Verify ROMs against No-Intro/Redump hashes.

    Checks that your ROMs match official database hashes.
    """
    from pier.roms.hashing import compute_sha1
    from pier.roms.systems import SYSTEMS

    if system and system not in SYSTEMS:
        console.print(f"[red]Unknown system: {system}[/red]")
        raise SystemExit(1)

    config = Config.load()

    if not config.roms_dir.exists():
        console.print(f"[red]ROM directory does not exist: {config.roms_dir}[/red]")
        raise SystemExit(1)

    # Get ROMs to verify
    games = scan_roms(config.roms_dir, system_filter=system)

    if not games:
        console.print("[yellow]No ROMs found to verify.[/yellow]")
        return

    console.print(f"[bold]Verifying {len(games)} ROM(s)...[/bold]")
    console.print()

    # Group by system for display
    by_system: dict[str, list] = {}
    for game in games:
        by_system.setdefault(game.system.id, []).append(game)

    for sys_id in sorted(by_system.keys()):
        sys_games = by_system[sys_id]
        sys_info = SYSTEMS[sys_id]

        console.print(f"[bold]{sys_info.name}[/bold]")

        for game in sys_games:
            sha1 = compute_sha1(game.path)
            # Just show the hash for now (DAT verification would require loading DAT files)
            console.print(f"  {game.display_name}")
            console.print(f"    [dim]SHA1: {sha1}[/dim]")

        console.print()

    console.print("[dim]Note: Full DAT verification requires downloading DAT files from datomatic.no-intro.org[/dim]")


# --- PC Ports commands ---


def _fetch_port_artwork(port: object, install_dir: Path, api_key: str) -> None:
    """Fetch artwork for a port from SteamGridDB.

    Args:
        port: The port to fetch artwork for (Port instance).
        install_dir: Port installation directory.
        api_key: SteamGridDB API key.
    """
    from pier.ports.steam import create_port_shortcut
    from pier.steam.artwork import ArtworkType, get_artwork_status
    from pier.steam.steamgriddb import SteamGridDBClient

    # Get app ID for artwork paths
    shortcut = create_port_shortcut(port, install_dir)
    app_id = shortcut["appid"]

    # Get current artwork status
    artwork = get_artwork_status(app_id)
    if not artwork or artwork.complete:
        return

    # Search for game on SteamGridDB
    search_name = port.steamgriddb_name or port.name
    client = SteamGridDBClient(api_key)

    try:
        games = client.search_game(search_name)
        if not games:
            return

        game = games[0]

        # Fetch missing artwork
        if not artwork.has_poster:
            grids = client.get_grids(game.id)
            if grids:
                dest = artwork.get_dest_path(ArtworkType.POSTER)
                client.download_image(grids[0].url, dest)

        if not artwork.has_hero:
            heroes = client.get_heroes(game.id)
            if heroes:
                dest = artwork.get_dest_path(ArtworkType.HERO)
                client.download_image(heroes[0].url, dest)

        if not artwork.has_logo:
            logos = client.get_logos(game.id)
            if logos:
                dest = artwork.get_dest_path(ArtworkType.LOGO)
                client.download_image(logos[0].url, dest)

        if not artwork.has_icon:
            icons = client.get_icons(game.id)
            if icons:
                dest = artwork.get_dest_path(ArtworkType.ICON)
                client.download_image(icons[0].url, dest)

    finally:
        client.close()


@cli.group(invoke_without_command=True)
@click.pass_context
def ports(ctx: click.Context) -> None:
    """Manage PC game ports (Ship of Harkinian, etc.).

    Without a subcommand, lists installed ports.
    """
    if ctx.invoked_subcommand is None:
        # Default to listing installed ports
        ctx.invoke(ports_installed)


@ports.command("installed")
def ports_installed() -> None:
    """List installed PC ports."""
    from pier.ports import scan_installed_ports

    config = Config.load()
    ports_dir = config.data_dir / "ports"

    installed = scan_installed_ports(ports_dir)

    if not installed:
        console.print("[yellow]No ports installed.[/yellow]")
        console.print("[dim]Use 'pier ports available' to see available ports.[/dim]")
        return

    table = Table(title=f"Installed Ports ({len(installed)})")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Version")
    table.add_column("Steam", justify="center")

    for port in installed:
        version = port.version or "[dim]unknown[/dim]"
        steam_status = "[green]✓[/green]" if port.in_steam else "[dim]-[/dim]"

        table.add_row(port.id, port.port.name, version, steam_status)

    console.print(table)
    console.print()
    console.print("[dim]Use 'pier ports status' to check for updates.[/dim]")


@ports.command("available")
def ports_available() -> None:
    """List all available PC ports."""
    from pier.ports import PORTS, PortType, scan_installed_ports

    config = Config.load()
    ports_dir = config.data_dir / "ports"

    # Check which are installed
    installed = scan_installed_ports(ports_dir)
    installed_ids = {p.id for p in installed}

    table = Table(title="Available PC Ports")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Type", style="dim")
    table.add_column("ROM System")
    table.add_column("Installed", justify="center")

    for port in PORTS.values():
        type_str = {
            PortType.HARBOUR_MASTERS: "Harbour Masters",
            PortType.OPENGOAL: "OpenGOAL",
            PortType.DIRECT_PORT: "Direct Port",
        }.get(port.type, str(port.type))

        installed_status = "[green]✓[/green]" if port.id in installed_ids else ""

        table.add_row(port.id, port.name, type_str, port.system.upper(), installed_status)

    console.print(table)
    console.print()
    console.print("[dim]Use 'pier ports install <id>' to install a port.[/dim]")


@ports.command("install")
@click.argument("port_id")
@click.option("--no-steam", is_flag=True, help="Don't add to Steam")
@click.option("--no-hd-textures", is_flag=True, help="Don't install HD texture packs")
def ports_install(port_id: str, no_steam: bool, no_hd_textures: bool) -> None:
    """Install a PC port.

    Downloads the port, finds/downloads the required ROM, generates assets,
    installs HD textures if available, and adds to Steam.
    """
    from pier.ports import PORTS, InstallerError, install_port

    if port_id not in PORTS:
        console.print(f"[red]Unknown port: {port_id}[/red]")
        console.print(f"Available ports: {', '.join(PORTS.keys())}")
        raise SystemExit(1)

    port = PORTS[port_id]
    config = Config.load()

    # Get ports directory
    ports_dir = config.data_dir / "ports"

    console.print(f"[bold]Installing {port.name}...[/bold]")
    console.print()

    def progress_callback(status: str, downloaded: int, total: int) -> None:
        if total > 0:
            pct = downloaded * 100 // total
            console.print(f"  {status} [{pct}%]", end="\r")
        else:
            console.print(f"  {status}")

    try:
        result = install_port(
            port=port,
            roms_dir=config.roms_dir,
            ports_dir=ports_dir,
            github_token=config.github_token,
            progress_callback=progress_callback,
            install_enhancements=not no_hd_textures,
        )

        console.print()

        if result.success:
            console.print(f"[green]\u2713 {port.name} installed successfully![/green]")
            console.print(f"  Version: {result.version}")
            console.print(f"  Location: {result.install_dir}")

            if result.rom_path:
                console.print(f"  ROM: {result.rom_path.name}")

            for warning in result.warnings:
                console.print(f"  [yellow]Warning: {warning}[/yellow]")

            if not no_steam and result.install_dir:
                from pier.ports import sync_port_to_steam
                from pier.steam.paths import find_steam_userdata

                if find_steam_userdata():
                    console.print()
                    console.print("  Adding to Steam...", end=" ")
                    try:
                        sync_port_to_steam(port, result.install_dir)
                        console.print("[green]done[/green]")

                        # Fetch artwork if SteamGridDB key configured
                        if config.steamgriddb_api_key and port.steamgriddb_name:
                            console.print("  Fetching artwork...", end=" ")
                            try:
                                _fetch_port_artwork(port, result.install_dir, config.steamgriddb_api_key)
                                console.print("[green]done[/green]")
                            except Exception:
                                console.print("[yellow]skipped[/yellow]")

                        if is_steam_running():
                            console.print()
                            console.print("[yellow]Note: Restart Steam to see the new shortcut.[/yellow]")
                    except Exception as e:
                        console.print(f"[yellow]failed: {e}[/yellow]")
                else:
                    console.print()
                    console.print("[dim]Steam not found - shortcut not created[/dim]")

        else:
            console.print("[red]\u2717 Installation failed[/red]")
            for error in result.errors:
                console.print(f"  [red]{error}[/red]")
            raise SystemExit(1)

    except InstallerError as e:
        console.print(f"[red]Installation error: {e}[/red]")
        raise SystemExit(1) from None


@ports.command("info")
@click.argument("port_id")
def ports_info(port_id: str) -> None:
    """Show information about a port."""
    from pier.ports import PORTS

    if port_id not in PORTS:
        console.print(f"[red]Unknown port: {port_id}[/red]")
        raise SystemExit(1)

    port = PORTS[port_id]

    console.print(f"[bold]{port.name}[/bold]")
    console.print()

    console.print(f"[cyan]ID:[/cyan] {port.id}")
    console.print(f"[cyan]Type:[/cyan] {port.type.value}")
    console.print(f"[cyan]GitHub:[/cyan] https://github.com/{port.github_repo}")
    console.print(f"[cyan]ROM System:[/cyan] {port.system.upper()}")
    console.print(f"[cyan]ROM Search:[/cyan] {port.rom_search_name}")

    if port.required_hashes:
        console.print(f"[cyan]Required Hashes:[/cyan] {len(port.required_hashes)} supported ROM(s)")
    else:
        console.print("[cyan]Required Hashes:[/cyan] [yellow]Not specified[/yellow]")

    if port.texture_pack_repo:
        console.print(f"[cyan]HD Textures:[/cyan] https://github.com/{port.texture_pack_repo}")

    if port.steamgriddb_name:
        console.print(f"[cyan]SteamGridDB:[/cyan] {port.steamgriddb_name}")


@ports.command("status")
def ports_status() -> None:
    """Show installed ports and check for updates."""
    from pier.ports import check_all_for_updates, scan_installed_ports

    config = Config.load()
    ports_dir = config.data_dir / "ports"

    installed = scan_installed_ports(ports_dir)

    if not installed:
        console.print("[yellow]No ports installed.[/yellow]")
        return

    console.print("[bold]Checking for updates...[/bold]")
    console.print()

    def progress_callback(msg: str) -> None:
        console.print(f"  {msg}")

    updates = check_all_for_updates(
        installed,
        github_token=config.github_token,
        progress_callback=progress_callback,
    )

    console.print()

    table = Table(title="Port Status")
    table.add_column("Name", style="cyan")
    table.add_column("Installed")
    table.add_column("Latest")
    table.add_column("Status", justify="center")

    for port in installed:
        installed_version = port.version or "unknown"
        if port.id in updates:
            latest = updates[port.id]
            status = "[yellow]Update available[/yellow]"
        else:
            latest = installed_version
            status = "[green]Up to date[/green]"

        table.add_row(port.port.name, installed_version, latest, status)

    console.print(table)

    if updates:
        console.print()
        console.print(f"[yellow]{len(updates)} update(s) available.[/yellow]")
        console.print("[dim]Use 'pier ports update [id]' to update.[/dim]")


@ports.command("update")
@click.argument("port_id", required=False)
@click.option("--all", "update_all", is_flag=True, help="Update all ports")
def ports_update(port_id: str | None, update_all: bool) -> None:
    """Update an installed port to the latest version.

    Specify PORT_ID to update a specific port, or use --all to update all.
    """
    from pier.ports import (
        PORTS,
        check_for_update,
        install_port,
        scan_installed_ports,
    )

    config = Config.load()
    ports_dir = config.data_dir / "ports"

    if not port_id and not update_all:
        console.print("[red]Specify a port ID or use --all[/red]")
        raise SystemExit(1)

    installed = scan_installed_ports(ports_dir)

    if not installed:
        console.print("[yellow]No ports installed.[/yellow]")
        return

    # Determine which ports to update
    if update_all:
        ports_to_update = installed
    else:
        ports_to_update = [p for p in installed if p.id == port_id]
        if not ports_to_update:
            console.print(f"[red]Port not installed: {port_id}[/red]")
            raise SystemExit(1)

    updated_count = 0

    for installed_port in ports_to_update:
        console.print(f"\n[bold]Checking {installed_port.port.name}...[/bold]")

        new_version = check_for_update(installed_port, config.github_token)
        if not new_version:
            console.print(f"  [green]Already up to date ({installed_port.version})[/green]")
            continue

        console.print(f"  Updating {installed_port.version} → {new_version}")

        port = PORTS[installed_port.id]

        def progress_callback(status: str, downloaded: int, total: int) -> None:
            if total > 0:
                pct = downloaded * 100 // total
                console.print(f"  {status} [{pct}%]", end="\r")
            else:
                console.print(f"  {status}")

        result = install_port(
            port=port,
            roms_dir=config.roms_dir,
            ports_dir=ports_dir,
            github_token=config.github_token,
            progress_callback=progress_callback,
        )

        if result.success:
            console.print(f"  [green]✓ Updated to {result.version}[/green]")
            updated_count += 1
        else:
            console.print("  [red]✗ Update failed[/red]")
            for error in result.errors:
                console.print(f"    [red]{error}[/red]")

    console.print()
    if updated_count > 0:
        console.print(f"[green]Updated {updated_count} port(s).[/green]")
        if is_steam_running():
            console.print("[yellow]Note: Restart Steam to see changes.[/yellow]")
    else:
        console.print("[dim]No updates installed.[/dim]")


@ports.command("remove")
@click.argument("port_id")
@click.option("--keep-rom", is_flag=True, help="Keep the ROM file")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def ports_remove(port_id: str, keep_rom: bool, yes: bool) -> None:
    """Remove an installed port.

    This removes the port installation and Steam shortcut.
    """
    from pier.ports import get_installed_port, remove_port

    config = Config.load()
    ports_dir = config.data_dir / "ports"

    installed = get_installed_port(port_id, ports_dir)

    if not installed:
        console.print(f"[red]Port not installed: {port_id}[/red]")
        raise SystemExit(1)

    if not yes:
        console.print(f"Remove port: [cyan]{installed.port.name}[/cyan]?")
        if installed.rom_path and not keep_rom:
            console.print(f"  [yellow]This will also remove the ROM: {installed.rom_path.name}[/yellow]")
            console.print("  [dim]Use --keep-rom to preserve it.[/dim]")
        if not click.confirm("Proceed?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    removed = remove_port(
        port_id,
        ports_dir,
        remove_rom=not keep_rom,
        remove_from_steam=True,
    )

    if removed:
        console.print(f"[green]Removed: {installed.port.name}[/green]")
        if is_steam_running():
            console.print("[yellow]Note: Restart Steam to see changes.[/yellow]")
    else:
        console.print("[red]Failed to remove port.[/red]")
        raise SystemExit(1)


@ports.command("enhancements")
@click.argument("port_id")
@click.argument("enhancement_id", required=False)
@click.option("--install", "do_install", is_flag=True, help="Install the enhancement")
def ports_enhancements(port_id: str, enhancement_id: str | None, do_install: bool) -> None:
    """List or install enhancements for a port.

    Without --install, lists available enhancements.
    With --install, downloads and installs the enhancement.
    """
    from pier.ports import (
        PORTS,
        EnhancementError,
        download_enhancement,
        get_installed_enhancements,
        get_installed_port,
    )

    if port_id not in PORTS:
        console.print(f"[red]Unknown port: {port_id}[/red]")
        raise SystemExit(1)

    port = PORTS[port_id]
    config = Config.load()
    ports_dir = config.data_dir / "ports"

    # Check if port is installed (needed for enhancements)
    installed = get_installed_port(port_id, ports_dir)

    if not port.enhancements:
        console.print(f"[yellow]No enhancements available for {port.name}.[/yellow]")
        return

    # If no enhancement specified, list them
    if not enhancement_id and not do_install:
        installed_ids = get_installed_enhancements(port, installed.install_dir) if installed else []

        table = Table(title=f"Enhancements for {port.name}")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Installed", justify="center")

        for enh in port.enhancements:
            status = "[green]✓[/green]" if enh.id in installed_ids else ""
            table.add_row(enh.id, enh.name, status)

        console.print(table)

        if not installed:
            console.print()
            console.print(f"[yellow]Note: {port.name} is not installed.[/yellow]")
            console.print("[dim]Install the port first with 'pier ports install'.[/dim]")
        else:
            console.print()
            console.print("[dim]Use 'pier ports enhancements <port> <id> --install' to install.[/dim]")
        return

    # Install an enhancement
    if not installed:
        console.print(f"[red]Port not installed: {port_id}[/red]")
        console.print(f"[dim]Install {port.name} first with 'pier ports install {port_id}'[/dim]")
        raise SystemExit(1)

    # Find the enhancement
    if enhancement_id:
        enhancement = None
        for enh in port.enhancements:
            if enh.id == enhancement_id:
                enhancement = enh
                break

        if not enhancement:
            console.print(f"[red]Unknown enhancement: {enhancement_id}[/red]")
            console.print(f"Available: {', '.join(e.id for e in port.enhancements)}")
            raise SystemExit(1)
    else:
        # Use first enhancement if none specified
        enhancement = port.enhancements[0]

    if not do_install:
        console.print(f"[yellow]Specify --install to download {enhancement.name}[/yellow]")
        return

    console.print(f"[bold]Installing {enhancement.name}...[/bold]")
    console.print()

    def progress_callback(status: str, downloaded: int, total: int) -> None:
        if total > 0:
            pct = downloaded * 100 // total
            console.print(f"  {status} [{pct}%]", end="\r")
        else:
            console.print(f"  {status}")

    try:
        dest_path = download_enhancement(
            port=port,
            enhancement=enhancement,
            install_dir=installed.install_dir,
            github_token=config.github_token,
            progress_callback=progress_callback,
        )

        console.print()
        console.print(f"[green]✓ Installed: {dest_path.name}[/green]")
        console.print(f"  Location: {dest_path.parent}")

    except EnhancementError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from None


if __name__ == "__main__":
    cli()
