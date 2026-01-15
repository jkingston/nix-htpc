"""Pier CLI - ROM management for NixOS HTPC."""

import click
from rich.console import Console
from rich.table import Table

from pier import __version__
from pier.config import Config
from pier.roms.scanner import scan_roms
from pier.roms.systems import SYSTEMS
from pier.steam.paths import find_shortcuts_vdf, find_steam_userdata
from pier.steam.shortcuts import get_pier_shortcuts, load_shortcuts, sync_games
from pier.steam.manager import (
    get_all_shortcuts,
    find_shortcut,
    remove_shortcut,
    get_shortcut_details,
)
from pier.steamgriddb import SteamGridDBClient, SteamGridDBError

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
            table.add_row(game.name, steam_status, game.filename)

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
            console.print(f"  + {game.name} ({game.system.name})")

    if result.updated:
        console.print(f"[yellow]Updated {len(result.updated)} games:[/yellow]")
        for game in result.updated:
            console.print(f"  ~ {game.name} ({game.system.name})")

    if result.removed:
        console.print(f"[red]Removed {len(result.removed)} shortcuts:[/red]")
        for game_id in result.removed:
            console.print(f"  - {game_id}")

    if result.unchanged:
        console.print(f"[dim]Unchanged: {len(result.unchanged)} games[/dim]")

    total_changes = len(result.added) + len(result.updated) + len(result.removed)
    if total_changes > 0 and not dry_run:
        console.print("\n[bold]Restart Steam to see changes.[/bold]")
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


# --- Shortcuts command group ---


@cli.group(invoke_without_command=True)
@click.pass_context
def shortcuts(ctx: click.Context) -> None:
    """Manage Steam shortcuts.

    Without a subcommand, lists all non-Steam shortcuts.
    """
    if ctx.invoked_subcommand is None:
        # Default to listing shortcuts
        ctx.invoke(shortcuts_list)


@shortcuts.command("list")
def shortcuts_list() -> None:
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
                "[dim]-[/dim]",
                "[dim]-[/dim]",
                "[dim]-[/dim]",
                "[dim]-[/dim]",
            )

    console.print(table)

    if missing_count > 0:
        console.print(f"\n[yellow]Missing artwork: {missing_count} shortcut(s)[/yellow]")
        console.print("[dim]Run 'pier shortcuts fetch-artwork' to download[/dim]")


@shortcuts.command("info")
@click.argument("query")
def shortcuts_info(query: str) -> None:
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


@shortcuts.command("remove")
@click.argument("query")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def shortcuts_remove(query: str, yes: bool) -> None:
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
        console.print("\n[bold]Restart Steam to see changes.[/bold]")
    else:
        console.print("[red]Failed to remove shortcut.[/red]")
        raise SystemExit(1)


@shortcuts.command("fetch-artwork")
@click.argument("query", required=False)
@click.option("--all", "fetch_all", is_flag=True, help="Fetch artwork for all shortcuts")
def shortcuts_fetch_artwork(query: str | None, fetch_all: bool) -> None:
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
                        if client.download_image(grids[0].url, artwork.paths.poster):
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
                        if client.download_image(heroes[0].url, artwork.paths.hero):
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
                        if client.download_image(logos[0].url, artwork.paths.logo):
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
                        if client.download_image(icons[0].url, artwork.paths.icon):
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
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
