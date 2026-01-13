"""CLI interface for pier."""

import asyncio
import sys

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from pier.core.constants import PIER_TAG, PORTS_TAG, STEAM_RUN_EXECUTABLE

console = Console()


def make_download_progress(progress: Progress, task_id) -> callable:
    """Create a download progress callback for a Progress task.

    Args:
        progress: The Rich Progress instance
        task_id: The task ID from progress.add_task()

    Returns:
        A callback function that updates progress as bytes are downloaded
    """
    def callback(downloaded: int, total: int):
        if progress.tasks[task_id].total is None and total > 0:
            progress.update(task_id, total=total)
        progress.update(task_id, completed=downloaded)
    return callback


@click.group(invoke_without_command=True)
@click.pass_context
@click.version_option()
def main(ctx: click.Context) -> None:
    """pier - HTPC game management tool.

    Manage ROMs, native game ports, and Steam integration from your terminal.
    """
    if ctx.invoked_subcommand is None:
        # Launch TUI if no subcommand
        from pier.tui.app import PierApp

        app = PierApp()
        app.run()


@main.command("list")
def list_cmd() -> None:
    """List installed ports and available ports."""
    from pier.core.config import Config, Library
    from pier.core.registry import list_ports

    config = Config.load()
    library = Library.load(config.pier_dir)

    table = Table(title="Game Ports")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Game")
    table.add_column("Status")
    table.add_column("Steam")

    for port in list_ports():
        installed = port.id in library.installed_ports
        version = library.installed_ports.get(port.id, {}).get("version", "")
        steam_linked = library.is_linked_to_steam(port.id)

        status = f"[green]v{version}[/green]" if installed else "[dim]not installed[/dim]"
        steam = "[green]linked[/green]" if steam_linked else "[dim]-[/dim]"

        table.add_row(port.id, port.name, port.game, status, steam)

    console.print(table)


@main.command()
@click.argument("port_id")
@click.option("--no-mods", is_flag=True, help="Skip HD texture pack installation")
@click.option("--no-steam", is_flag=True, help="Don't add to Steam library")
@click.option("--no-artwork", is_flag=True, help="Don't fetch artwork")
def install(port_id: str, no_mods: bool, no_steam: bool, no_artwork: bool) -> None:
    """Install a native game port."""
    from pier.core.registry import get_port
    from pier.core.installer import PortInstaller, InstallError, ProgressReporter

    port = get_port(port_id)
    if not port:
        console.print(f"[red]Unknown port: {port_id}[/red]")
        console.print("\nAvailable ports:")
        from pier.core.registry import list_ports
        for p in list_ports():
            console.print(f"  {p.id}: {p.name} ({p.game})")
        sys.exit(1)

    console.print(f"[bold]Installing {port.name}[/bold]")
    console.print(f"Game: {port.game}")
    console.print(f"ROM required: {port.rom.name}")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Starting...", total=None)
        download_task = None

        def on_status(message: str):
            progress.update(task, description=message)

        def on_progress(downloaded: int, total: int):
            nonlocal download_task
            if download_task is None and total > 0:
                download_task = progress.add_task("Downloading...", total=total)
            if download_task is not None:
                progress.update(download_task, completed=downloaded)

        async def _install():
            reporter = ProgressReporter(on_status, on_progress)
            installer = PortInstaller(progress=reporter)
            return await installer.install(
                port_id,
                with_mods=not no_mods,
                add_to_steam=not no_steam,
                fetch_artwork=not no_artwork,
            )

        try:
            result = asyncio.run(_install())
            progress.update(task, description="[green]Complete![/green]")
        except InstallError as e:
            progress.update(task, description=f"[red]Failed: {e}[/red]")
            sys.exit(1)

    console.print()
    console.print(f"[green]Successfully installed {port.name}![/green]")
    console.print(f"Location: {result}")
    if not no_steam:
        console.print("Steam shortcut created - restart Steam to see it")


@main.command()
@click.argument("port_id", required=False)
@click.option("--auto", is_flag=True, help="Non-interactive mode for systemd")
def update(port_id: str | None, auto: bool) -> None:
    """Check for and apply updates."""
    from pier.core.installer import check_updates_sync, PortInstaller, ProgressReporter
    import asyncio

    if port_id:
        # Update specific port
        async def _update():
            installer = PortInstaller()
            result = await installer.check_update(port_id)
            if result:
                current, new = result
                console.print(f"Update available: {current} -> {new}")
                if auto or click.confirm("Update now?"):
                    await installer.update(port_id)
                    console.print("[green]Updated successfully![/green]")
            else:
                console.print("Already up to date")

        asyncio.run(_update())
    else:
        # Check all ports
        updates = check_updates_sync()
        if not updates:
            console.print("All ports are up to date")
            return

        table = Table(title="Available Updates")
        table.add_column("Port")
        table.add_column("Current")
        table.add_column("New")

        for port_id, current, new in updates:
            table.add_row(port_id, current, new)

        console.print(table)

        if auto or click.confirm("Update all?"):
            async def _update_all():
                installer = PortInstaller()
                for port_id, _, _ in updates:
                    console.print(f"Updating {port_id}...")
                    await installer.update(port_id)
                console.print("[green]All ports updated![/green]")

            asyncio.run(_update_all())


@main.group()
def roms() -> None:
    """ROM management commands."""
    pass


@roms.command("list")
@click.argument("system")
@click.option("--limit", "-n", default=50, help="Maximum results")
def roms_list(system: str, limit: int) -> None:
    """List ROMs for a system from myrient."""
    from pier.core.registry import SYSTEMS
    from pier.core.myrient import MyrientBrowser
    import asyncio

    if system not in SYSTEMS:
        console.print(f"[red]Unknown system: {system}[/red]")
        console.print("Available systems:", ", ".join(SYSTEMS.keys()))
        sys.exit(1)

    console.print(f"Listing ROMs for {SYSTEMS[system].name}...")

    async def _list():
        browser = MyrientBrowser()
        try:
            entries = await browser.list_system(system)
            return entries
        finally:
            await browser.close()

    entries = asyncio.run(_list())
    roms = [e for e in entries if not e.is_directory][:limit]

    table = Table(title=f"{SYSTEMS[system].name} ROMs ({len(roms)} shown)")
    table.add_column("Name")
    table.add_column("Size", justify="right")

    for rom in roms:
        table.add_row(rom.name, rom.size)

    console.print(table)


@roms.command("search")
@click.argument("system")
@click.argument("query")
@click.option("--limit", "-n", default=20, help="Maximum results")
def roms_search(system: str, query: str, limit: int) -> None:
    """Search ROMs on myrient."""
    from pier.core.registry import SYSTEMS
    from pier.core.myrient import MyrientBrowser
    import asyncio

    if system not in SYSTEMS:
        console.print(f"[red]Unknown system: {system}[/red]")
        sys.exit(1)

    console.print(f"Searching {SYSTEMS[system].name} for '{query}'...")

    async def _search():
        browser = MyrientBrowser()
        try:
            return await browser.search(system, query, limit)
        finally:
            await browser.close()

    results = asyncio.run(_search())

    if not results:
        console.print("No results found")
        return

    table = Table(title=f"Search Results ({len(results)})")
    table.add_column("Name")
    table.add_column("Size", justify="right")

    for rom in results:
        table.add_row(rom.name, rom.size)

    console.print(table)


@roms.command("download")
@click.argument("system")
@click.argument("filename")
def roms_download(system: str, filename: str) -> None:
    """Download a ROM from myrient."""
    from pier.core.registry import SYSTEMS
    from pier.core.myrient import MyrientBrowser
    from pier.core.config import Config
    import asyncio
    from urllib.parse import quote

    if system not in SYSTEMS:
        console.print(f"[red]Unknown system: {system}[/red]")
        sys.exit(1)

    config = Config.load()
    dest_dir = config.roms_dir / system

    console.print(f"Downloading {filename}...")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Downloading...", total=None)
        on_progress = make_download_progress(progress, task)

        async def _download():
            browser = MyrientBrowser()
            try:
                path = f"{SYSTEMS[system].myrient_path}/{quote(filename)}"
                return await browser.download(path, dest_dir, on_progress)
            finally:
                await browser.close()

        result = asyncio.run(_download())

    console.print(f"[green]Downloaded:[/green] {result}")


@main.group()
def steam() -> None:
    """Steam shortcut management."""
    pass


@steam.command("sync")
def steam_sync() -> None:
    """Sync all linked games to Steam."""
    from pier.core.config import Config, Library
    from pier.core.steam import SteamLibrary
    from pier.core.registry import get_port

    config = Config.load()
    library = Library.load(config.pier_dir)

    try:
        steam = SteamLibrary()
    except FileNotFoundError:
        console.print("[red]Steam not found. Has Steam been run at least once?[/red]")
        sys.exit(1)

    synced = 0
    for port_id, info in library.installed_ports.items():
        if library.is_linked_to_steam(port_id):
            port = get_port(port_id)
            if port:
                exe_path = info.get("executable", "")
                if exe_path:
                    steam.add_shortcut(
                        app_name=port.name,
                        exe=STEAM_RUN_EXECUTABLE,
                        start_dir=str(config.ports_dir / port_id),
                        launch_options=f'"{exe_path}"',
                        tags=[PIER_TAG, PORTS_TAG],
                    )
                    synced += 1
                    console.print(f"  Synced: {port.name}")

    console.print(f"[green]Synced {synced} shortcuts[/green]")
    console.print("Restart Steam to see changes")


@steam.command("link")
@click.argument("game_id")
def steam_link(game_id: str) -> None:
    """Link a game to Steam library."""
    from pier.core.config import Config, Library

    config = Config.load()
    library = Library.load(config.pier_dir)

    library.set_steam_link(game_id, True)
    library.save(config.pier_dir)

    console.print(f"[green]Marked {game_id} for Steam linking[/green]")
    console.print("Run 'pier steam sync' to update shortcuts")


@steam.command("unlink")
@click.argument("game_id")
def steam_unlink(game_id: str) -> None:
    """Remove a game from Steam library."""
    from pier.core.config import Config, Library
    from pier.core.steam import SteamLibrary
    from pier.core.registry import get_port

    config = Config.load()
    library = Library.load(config.pier_dir)

    library.set_steam_link(game_id, False)
    library.save(config.pier_dir)

    # Also remove from Steam shortcuts
    port = get_port(game_id)
    if port:
        try:
            steam = SteamLibrary()
            if steam.remove_shortcut(port.name):
                console.print(f"Removed Steam shortcut for {port.name}")
        except FileNotFoundError:
            pass

    console.print(f"[green]Unlinked {game_id} from Steam[/green]")


@main.group()
def config() -> None:
    """Configuration management."""
    pass


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a configuration value."""
    from pier.core.config import Config

    cfg = Config.load()

    if not hasattr(cfg, key):
        console.print(f"[red]Unknown config key: {key}[/red]")
        console.print("Available keys: steamgriddb_api_key, auto_fetch_artwork, auto_add_to_steam, install_hd_textures")
        sys.exit(1)

    # Handle boolean values
    if value.lower() in ("true", "1", "yes"):
        value = True
    elif value.lower() in ("false", "0", "no"):
        value = False

    cfg.set(key, value)
    cfg.save()

    console.print(f"[green]Set {key} = {value}[/green]")


@config.command("get")
@click.argument("key", required=False)
def config_get(key: str | None) -> None:
    """Get configuration values."""
    from pier.core.config import Config

    cfg = Config.load()

    if key:
        value = cfg.get(key)
        if value is None:
            console.print(f"[red]Unknown key: {key}[/red]")
            sys.exit(1)
        console.print(f"{key} = {value}")
    else:
        # Show all config
        table = Table(title="Configuration")
        table.add_column("Key")
        table.add_column("Value")

        table.add_row("emulation_dir", str(cfg.emulation_dir))
        table.add_row("roms_dir", str(cfg.roms_dir))
        table.add_row("ports_dir", str(cfg.ports_dir))
        table.add_row("pier_dir", str(cfg.pier_dir))
        table.add_row("steamgriddb_api_key", "***" if cfg.steamgriddb_api_key else "(not set)")
        table.add_row("auto_fetch_artwork", str(cfg.auto_fetch_artwork))
        table.add_row("auto_add_to_steam", str(cfg.auto_add_to_steam))
        table.add_row("install_hd_textures", str(cfg.install_hd_textures))

        console.print(table)


@config.command("path")
def config_path() -> None:
    """Show configuration file paths."""
    from pier.core.config import Config

    cfg = Config.load()

    console.print(f"Config file: {cfg.pier_dir / 'config.json'}")
    console.print(f"Library file: {cfg.pier_dir / 'library.json'}")
    console.print(f"Ports directory: {cfg.ports_dir}")
    console.print(f"ROMs directory: {cfg.roms_dir}")


@main.group()
def bios() -> None:
    """BIOS file management."""
    pass


@bios.command("check")
def bios_check() -> None:
    """Check status of BIOS files."""
    from pier.core.bios import BiosManager, BiosStatus

    manager = BiosManager()
    results = manager.check_all()

    table = Table(title="BIOS Status")
    table.add_column("File", style="cyan")
    table.add_column("System")
    table.add_column("Status")
    table.add_column("Hash", style="dim")

    for result in results:
        if result.status == BiosStatus.VALID:
            status = "[green]Valid[/green]"
            hash_display = result.actual_md5[:8] if result.actual_md5 else "-"
        elif result.status == BiosStatus.INVALID:
            status = "[red]Invalid[/red]"
            hash_display = f"[red]{result.actual_md5[:8]}[/red]" if result.actual_md5 else "-"
        else:
            status = "[dim]Missing[/dim]"
            hash_display = "-"

        table.add_row(
            result.bios.filename,
            result.bios.system.upper(),
            status,
            hash_display,
        )

    console.print(table)

    # Summary
    valid = sum(1 for r in results if r.status == BiosStatus.VALID)
    invalid = sum(1 for r in results if r.status == BiosStatus.INVALID)
    missing = sum(1 for r in results if r.status == BiosStatus.MISSING)

    console.print()
    console.print(f"Valid: {valid}, Invalid: {invalid}, Missing: {missing}")
    console.print(f"BIOS directory: {manager.bios_dir}")


@bios.command("list")
@click.argument("system", required=False)
def bios_list(system: str | None) -> None:
    """List available BIOS files."""
    from pier.core.bios import BIOS_REGISTRY, get_bios_by_system

    if system:
        files = get_bios_by_system(system.lower())
        if not files:
            console.print(f"[red]Unknown system: {system}[/red]")
            systems = sorted(set(b.system for b in BIOS_REGISTRY))
            console.print(f"Available systems: {', '.join(systems)}")
            sys.exit(1)
    else:
        files = BIOS_REGISTRY

    table = Table(title="Available BIOS Files")
    table.add_column("File", style="cyan")
    table.add_column("System")
    table.add_column("Priority")
    table.add_column("Description")

    for bios in files:
        priority = "[green]Recommended[/green]" if bios.priority == 1 else "[dim]Optional[/dim]"
        table.add_row(
            bios.filename,
            bios.system.upper(),
            priority,
            bios.description,
        )

    console.print(table)


@bios.command("download")
@click.argument("filename", required=False)
@click.option("--all", "download_all", is_flag=True, help="Download all BIOS files")
def bios_download(filename: str | None, download_all: bool) -> None:
    """Download BIOS files from retroarch_system repo.

    Without arguments, downloads recommended files only.
    Specify a filename to download a specific file.
    Use --all to download all known BIOS files.
    """
    from pier.core.bios import (
        BiosManager,
        get_bios_by_filename,
        download_bios_sync,
        download_recommended_sync,
        download_all_sync,
    )
    import asyncio

    if filename:
        # Download specific file
        bios = get_bios_by_filename(filename)
        if not bios:
            console.print(f"[red]Unknown BIOS file: {filename}[/red]")
            console.print("Run 'pier bios list' to see available files")
            sys.exit(1)

        console.print(f"Downloading {bios.filename}...")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Downloading...", total=None)
            on_progress = make_download_progress(progress, task)

            try:
                path = download_bios_sync(filename, on_progress)
                console.print(f"[green]Downloaded:[/green] {path}")
            except ValueError as e:
                console.print(f"[red]Error:[/red] {e}")
                sys.exit(1)

    elif download_all:
        # Download all BIOS files
        console.print("Downloading all BIOS files...")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            current_task = None
            current_file = None

            def on_progress(filename: str, downloaded: int, total: int):
                nonlocal current_task, current_file
                if filename != current_file:
                    current_file = filename
                    current_task = progress.add_task(f"Downloading {filename}...", total=total or None)
                if current_task is not None:
                    if progress.tasks[current_task].total is None and total > 0:
                        progress.update(current_task, total=total)
                    progress.update(current_task, completed=downloaded)

            paths = download_all_sync(on_progress)

        if paths:
            console.print(f"[green]Downloaded {len(paths)} files[/green]")
        else:
            console.print("All BIOS files already present")

    else:
        # Download recommended files only
        console.print("Downloading recommended BIOS files...")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            current_task = None
            current_file = None

            def on_progress(filename: str, downloaded: int, total: int):
                nonlocal current_task, current_file
                if filename != current_file:
                    current_file = filename
                    current_task = progress.add_task(f"Downloading {filename}...", total=total or None)
                if current_task is not None:
                    if progress.tasks[current_task].total is None and total > 0:
                        progress.update(current_task, total=total)
                    progress.update(current_task, completed=downloaded)

            paths = download_recommended_sync(on_progress)

        if paths:
            console.print(f"[green]Downloaded {len(paths)} files[/green]")
        else:
            console.print("All recommended BIOS files already present")


if __name__ == "__main__":
    main()
