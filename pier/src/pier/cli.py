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
            data = load_shortcuts(shortcuts_path)
            pier_shortcuts = get_pier_shortcuts(data)
            console.print(f"  Pier shortcuts in Steam: {len(pier_shortcuts)}")
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


if __name__ == "__main__":
    cli()
