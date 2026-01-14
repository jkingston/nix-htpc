"""Steam sync management screen."""

import shutil
from enum import Enum
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
)

from pier.core.artwork import ArtworkSet, fetch_artwork_for_rom_sync
from pier.core.artwork_cache import ArtworkCache
from pier.core.config import CustomGame
from pier.core.constants import (
    GAME_ID_CUSTOM_PREFIX,
    GAME_ID_ROM_PREFIX,
    PIER_TAG,
    PORTS_TAG,
    STEAM_RUN_EXECUTABLE,
)
from pier.core.registry import SYSTEMS, get_port, get_system
from pier.core.steam import Shortcut, SteamLibrary
from pier.tui.screens.base import PierScreen


class GameStatus(Enum):
    """Status of a game relative to Steam."""

    IN_STEAM = "in_steam"  # Already in Steam shortcuts
    WILL_ADD = "will_add"  # Will be added on sync
    HIDDEN = "hidden"  # User chose to hide from Steam
    UNTRACKED = "untracked"  # File exists but not in pier library


class GameEntry:
    """A game entry for the Steam sync table."""

    def __init__(
        self,
        game_id: str,
        name: str,
        game_type: str,
        status: GameStatus,
        file_path: Path | None = None,
    ):
        self.game_id = game_id
        self.name = name
        self.game_type = game_type
        self.status = status
        self.file_path = file_path

    @property
    def status_display(self) -> str:
        """Get formatted status for display."""
        if self.status == GameStatus.IN_STEAM:
            return "[green]✓ In Steam[/green]"
        elif self.status == GameStatus.WILL_ADD:
            return "[cyan]+ Will add[/cyan]"
        elif self.status == GameStatus.HIDDEN:
            return "[dim]⊘ Hidden[/dim]"
        else:
            return "[yellow]? Untracked[/yellow]"


class AddCustomDialog(ModalScreen[CustomGame | None]):
    """Modal dialog for adding a custom game."""

    CSS = """
    AddCustomDialog {
        align: center middle;
    }

    #dialog-container {
        width: 70;
        height: auto;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    #dialog-title {
        text-align: center;
        text-style: bold;
        padding-bottom: 1;
    }

    .field-row {
        height: auto;
        margin-bottom: 1;
    }

    .field-label {
        width: 12;
    }

    .field-input {
        width: 1fr;
    }

    #dialog-actions {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    #dialog-actions Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        yield Container(
            Static("Add Custom Game", id="dialog-title"),
            Horizontal(
                Label("Name:", classes="field-label"),
                Input(placeholder="Game name", id="input-name", classes="field-input"),
                classes="field-row",
            ),
            Horizontal(
                Label("Executable:", classes="field-label"),
                Input(placeholder="/path/to/game", id="input-exe", classes="field-input"),
                classes="field-row",
            ),
            Horizontal(
                Label("Start Dir:", classes="field-label"),
                Input(placeholder="(auto from exe)", id="input-dir", classes="field-input"),
                classes="field-row",
            ),
            Horizontal(
                Label("Args:", classes="field-label"),
                Input(placeholder="(optional)", id="input-args", classes="field-input"),
                classes="field-row",
            ),
            Horizontal(
                Checkbox("Use steam-run (for Windows .exe)", id="check-steamrun"),
                classes="field-row",
            ),
            Horizontal(
                Button("Add", id="btn-add", variant="primary"),
                Button("Cancel", id="btn-cancel", variant="default"),
                id="dialog-actions",
            ),
            id="dialog-container",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
        elif event.button.id == "btn-add":
            self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        name = self.query_one("#input-name", Input).value.strip()
        exe = self.query_one("#input-exe", Input).value.strip()
        start_dir = self.query_one("#input-dir", Input).value.strip()
        args = self.query_one("#input-args", Input).value.strip()
        use_steam_run = self.query_one("#check-steamrun", Checkbox).value

        if not name or not exe:
            return

        # Auto-fill start_dir if not provided
        if not start_dir:
            start_dir = str(Path(exe).parent)

        game = CustomGame(
            name=name,
            executable=exe,
            start_dir=start_dir,
            launch_args=args,
            use_steam_run=use_steam_run,
        )
        self.dismiss(game)


class SteamScreen(PierScreen):
    """Screen for managing Steam shortcuts."""

    CSS = """
    SteamScreen {
        align: center middle;
    }

    #steam-container {
        width: 90%;
        height: 90%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    #steam-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        padding-bottom: 1;
    }

    #steam-table {
        height: 1fr;
        margin-bottom: 1;
    }

    #steam-summary {
        text-align: center;
        padding: 1 0;
    }

    #steam-actions {
        height: auto;
        align: center middle;
    }

    #steam-actions Button {
        margin: 0 1;
    }

    #steam-info {
        text-align: center;
        color: $text-muted;
        padding-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("s", "sync", "Sync"),
        Binding("h", "toggle_hidden", "Hide/Show"),
        Binding("r", "remove", "Remove"),
        Binding("d", "delete", "Delete"),
        Binding("c", "add_custom", "Add Custom"),
        Binding("i", "install_game", "Install"),
        Binding("x", "scan_roms", "Scan ROMs"),
        Binding("a", "artwork", "Artwork"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._entries: list[GameEntry] = []
        self._selected_entry: GameEntry | None = None
        self._steam: SteamLibrary | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static("Steam Sync", id="steam-title"),
            DataTable(id="steam-table", cursor_type="row"),
            Static("", id="steam-summary"),
            Horizontal(
                Button("Sync", id="btn-sync", variant="primary"),
                Button("Artwork", id="btn-artwork", variant="default"),
                Button("Hide/Show", id="btn-toggle", variant="default"),
                Button("Remove", id="btn-remove", variant="default"),
                Button("Delete", id="btn-delete", variant="warning"),
                Button("Add Custom", id="btn-custom", variant="default"),
                Button("Install", id="btn-install", variant="default"),
                Button("Scan ROMs", id="btn-scan", variant="default"),
                id="steam-actions",
            ),
            Static("Select a game and press H to hide/show, S to sync", id="steam-info"),
            id="steam-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the table when mounted."""
        table = self.query_one("#steam-table", DataTable)
        table.add_columns("Game", "Type", "Status")
        self._load_entries()

    def _load_entries(self) -> None:
        """Load all game entries and compute their status."""
        self.reload_library()
        self._entries.clear()

        # Try to load Steam shortcuts
        steam_shortcuts: set[str] = set()
        try:
            self._steam = SteamLibrary()
            for shortcut in self._steam.list_pier_shortcuts():
                steam_shortcuts.add(shortcut.app_name)
        except FileNotFoundError:
            self._steam = None

        # Add installed ports
        for port_id in self.library.installed_ports:
            port = get_port(port_id)
            if not port:
                continue

            if port.name in steam_shortcuts:
                status = GameStatus.IN_STEAM
            elif self.library.is_hidden_from_steam(port_id):
                status = GameStatus.HIDDEN
            else:
                status = GameStatus.WILL_ADD

            self._entries.append(
                GameEntry(
                    game_id=port_id,
                    name=port.name,
                    game_type="Port",
                    status=status,
                    file_path=self.config.ports_dir / port_id,
                )
            )

        # Add downloaded ROMs
        for system_id, roms in self.library.downloaded_roms.items():
            system = get_system(system_id)
            if not system:
                continue

            for rom_name in roms:
                game_id = f"{GAME_ID_ROM_PREFIX}{system_id}:{rom_name}"
                display_name = Path(rom_name).stem  # Name without extension

                if display_name in steam_shortcuts:
                    status = GameStatus.IN_STEAM
                elif self.library.is_hidden_from_steam(game_id):
                    status = GameStatus.HIDDEN
                else:
                    status = GameStatus.WILL_ADD

                self._entries.append(
                    GameEntry(
                        game_id=game_id,
                        name=display_name[:40] + "..." if len(display_name) > 40 else display_name,
                        game_type=f"ROM ({system_id.upper()})",
                        status=status,
                        file_path=self.config.roms_dir / system_id / rom_name,
                    )
                )

        # Add custom games
        for game_id, game_data in self.library.custom_games.items():
            name = game_data["name"]
            if name in steam_shortcuts:
                status = GameStatus.IN_STEAM
            elif self.library.is_hidden_from_steam(game_id):
                status = GameStatus.HIDDEN
            else:
                status = GameStatus.WILL_ADD

            self._entries.append(
                GameEntry(
                    game_id=game_id,
                    name=name,
                    game_type="Custom",
                    status=status,
                    file_path=Path(game_data["executable"]) if game_data.get("executable") else None,
                )
            )

        self._populate_table()

    def _populate_table(self) -> None:
        """Populate the table with entries."""
        table = self.query_one("#steam-table", DataTable)
        table.clear()

        for entry in self._entries:
            table.add_row(
                entry.name,
                entry.game_type,
                entry.status_display,
                key=entry.game_id,
            )

        self._update_summary()

    def _update_summary(self) -> None:
        """Update the sync summary."""
        in_steam = sum(1 for e in self._entries if e.status == GameStatus.IN_STEAM)
        will_add = sum(1 for e in self._entries if e.status == GameStatus.WILL_ADD)
        hidden = sum(1 for e in self._entries if e.status == GameStatus.HIDDEN)

        summary = self.query_one("#steam-summary", Static)
        parts = []
        if will_add:
            parts.append(f"[cyan]add {will_add}[/cyan]")
        if in_steam:
            parts.append(f"[green]{in_steam} synced[/green]")
        if hidden:
            parts.append(f"[dim]{hidden} hidden[/dim]")

        if parts:
            prefix = "Sync will: " if will_add else ""
            summary.update(prefix + ", ".join(parts))
        else:
            summary.update("No games to sync")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection."""
        if event.row_key:
            game_id = str(event.row_key.value)
            self._selected_entry = next((e for e in self._entries if e.game_id == game_id), None)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle row highlight."""
        if event.row_key:
            game_id = str(event.row_key.value)
            self._selected_entry = next((e for e in self._entries if e.game_id == game_id), None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id
        if button_id == "btn-sync":
            self.action_sync()
        elif button_id == "btn-artwork":
            self.action_artwork()
        elif button_id == "btn-toggle":
            self.action_toggle_hidden()
        elif button_id == "btn-remove":
            self.action_remove()
        elif button_id == "btn-delete":
            self.action_delete()
        elif button_id == "btn-custom":
            self.action_add_custom()
        elif button_id == "btn-install":
            self.action_install_game()
        elif button_id == "btn-scan":
            self.action_scan_roms()

    def action_toggle_hidden(self) -> None:
        """Toggle hide/show for selected game."""
        if not self._selected_entry:
            self.notify_warning("Select a game first")
            return

        entry = self._selected_entry
        if entry.status == GameStatus.IN_STEAM:
            self.notify_warning("Already in Steam - use Remove first, then Hide to prevent re-adding")
            return

        # Toggle hidden state
        is_hidden = self.library.is_hidden_from_steam(entry.game_id)
        self.library.set_hidden_from_steam(entry.game_id, not is_hidden)
        self.library.save(self.config.pier_dir)

        action = "hidden from" if not is_hidden else "will be added to"
        self.notify_info(f"{entry.name} {action} Steam")
        self._load_entries()

    def action_remove(self) -> None:
        """Remove selected game from Steam only."""
        if not self._selected_entry:
            self.notify_warning("Select a game first")
            return

        entry = self._selected_entry
        if entry.status != GameStatus.IN_STEAM:
            self.notify_warning("Game is not in Steam")
            return

        if not self._steam:
            self.notify_error("Steam not found")
            return

        # Remove from Steam
        if self._steam.remove_shortcut(entry.name):
            self.notify_success(f"Removed {entry.name} from Steam")
            self._load_entries()
        else:
            self.notify_error("Failed to remove from Steam")

    def action_delete(self) -> None:
        """Delete selected game entirely."""
        if not self._selected_entry:
            self.notify_warning("Select a game first")
            return

        entry = self._selected_entry

        # Remove from Steam if present
        if entry.status == GameStatus.IN_STEAM and self._steam:
            self._steam.remove_shortcut(entry.name)

        # Remove from library and delete files
        if entry.game_id.startswith(GAME_ID_ROM_PREFIX):
            parts = entry.game_id.split(":", 2)
            if len(parts) == 3:
                system_id, rom_name = parts[1], parts[2]
                self.library.remove_rom(system_id, rom_name)
                if entry.file_path and entry.file_path.exists():
                    entry.file_path.unlink()
        elif entry.game_id.startswith(GAME_ID_CUSTOM_PREFIX):
            self.library.remove_custom_game(entry.game_id)
        else:
            # Port
            self.library.remove_port(entry.game_id)
            if entry.file_path and entry.file_path.exists():
                shutil.rmtree(entry.file_path, ignore_errors=True)

        self.library.save(self.config.pier_dir)
        self.notify_success(f"Deleted {entry.name}")
        self._load_entries()

    def action_add_custom(self) -> None:
        """Open dialog to add a custom game."""

        def on_result(game: CustomGame | None) -> None:
            if game:
                # Generate unique ID
                game_id = f"{GAME_ID_CUSTOM_PREFIX}{game.name.lower().replace(' ', '_')}"
                self.library.add_custom_game(game_id, game)
                self.library.save(self.config.pier_dir)
                self.notify_success(f"Added {game.name}")
                self._load_entries()

        self.app.push_screen(AddCustomDialog(), on_result)

    def action_install_game(self) -> None:
        """Open install wizard for GOG/itch.io games."""
        from pier.tui.screens.install_wizard import InstallWizardScreen

        def on_dismiss(_: object) -> None:
            self._load_entries()

        self.app.push_screen(InstallWizardScreen(), on_dismiss)

    def action_scan_roms(self) -> None:
        """Scan ROM directories for untracked ROMs."""
        found = 0
        for system_id, system in SYSTEMS.items():
            rom_dir = self.config.roms_dir / system_id
            if not rom_dir.exists():
                continue

            known_roms = set(self.library.downloaded_roms.get(system_id, []))
            for rom_file in rom_dir.iterdir():
                if rom_file.suffix.lower() in system.extensions and rom_file.name not in known_roms:
                    self.library.add_rom(system_id, rom_file.name)
                    found += 1

        if found:
            self.library.save(self.config.pier_dir)
            self.notify_success(f"Found {found} new ROMs")
            self._load_entries()
        else:
            self.notify_info("No new ROMs found")

    def action_sync(self) -> None:
        """Sync all 'will add' games to Steam."""
        if not self._steam:
            self.notify_error("Steam not found. Has Steam been run at least once?")
            return

        will_add = [e for e in self._entries if e.status == GameStatus.WILL_ADD]
        if not will_add:
            self.notify_info("Nothing to sync")
            return

        self.notify_info("Syncing to Steam...")
        self.run_worker(self._do_sync(will_add), exclusive=True, thread=True)

    async def _do_sync(self, entries: list[GameEntry]) -> None:
        """Worker to sync games to Steam."""
        synced = 0
        failed = 0

        for entry in entries:
            try:
                if entry.game_id.startswith(GAME_ID_ROM_PREFIX):
                    self._sync_rom(entry)
                elif entry.game_id.startswith(GAME_ID_CUSTOM_PREFIX):
                    self._sync_custom(entry)
                else:
                    self._sync_port(entry)
                synced += 1
            except Exception as e:
                failed += 1
                self.app.call_from_thread(
                    self.notify_warning, f"Failed to sync {entry.name}: {e}"
                )

        if failed:
            self.app.call_from_thread(
                self.notify_warning, f"Synced {synced}, failed {failed}"
            )
        elif synced > 0:
            self.app.call_from_thread(
                self.notify_success, f"Synced {synced} games (appear next Steam launch)"
            )
        else:
            self.app.call_from_thread(self.notify_info, "Nothing to sync")

        self.app.call_from_thread(self._load_entries)

    def _sync_port(self, entry: GameEntry) -> None:
        """Sync a port to Steam."""
        if not self._steam:
            return

        port = get_port(entry.game_id)
        if not port:
            return

        info = self.library.installed_ports.get(entry.game_id, {})
        exe_path = info.get("executable", "")
        if not exe_path:
            return

        shortcut = self._steam.add_shortcut(
            app_name=port.name,
            exe=STEAM_RUN_EXECUTABLE,
            start_dir=str(self.config.ports_dir / entry.game_id),
            launch_options=f'"{exe_path}"',
            tags=[PIER_TAG, PORTS_TAG],
        )

        # Install artwork
        self._install_artwork_for_shortcut(entry, shortcut, port.game)

    def _sync_rom(self, entry: GameEntry) -> None:
        """Sync a ROM to Steam."""
        if not self._steam or not entry.file_path:
            return

        parts = entry.game_id.split(":", 2)
        if len(parts) != 3:
            return

        system_id = parts[1]
        system = get_system(system_id)
        if not system or not system.emulator_wrapper:
            return

        # Build launch options
        if system.emulator_args:
            # RetroArch style: wrapper core_name "rom_path"
            launch_options = f'{system.emulator_args} "{entry.file_path}"'
        else:
            # Standalone emulator: just "rom_path"
            launch_options = f'"{entry.file_path}"'

        shortcut = self._steam.add_shortcut(
            app_name=entry.file_path.stem,  # Name without extension
            exe=f"/run/current-system/sw/bin/{system.emulator_wrapper}",
            start_dir=str(entry.file_path.parent),
            launch_options=launch_options,
            tags=[PIER_TAG, system.name],
        )

        # Install artwork (use ROM filename without extension as title)
        rom_name = entry.file_path.stem
        self._install_artwork_for_shortcut(entry, shortcut, rom_name, system_id=system_id)

    def _sync_custom(self, entry: GameEntry) -> None:
        """Sync a custom game to Steam."""
        if not self._steam:
            return

        game = self.library.get_custom_game(entry.game_id)
        if not game:
            return

        if game.use_steam_run:
            # Wrap with steam-run for Windows executables
            exe = STEAM_RUN_EXECUTABLE
            launch_options = f'"{game.executable}"'
            if game.launch_args:
                launch_options += f" {game.launch_args}"
        else:
            exe = game.executable
            launch_options = game.launch_args

        shortcut = self._steam.add_shortcut(
            app_name=game.name,
            exe=exe,
            start_dir=game.start_dir,
            launch_options=launch_options,
            tags=[PIER_TAG, "Custom"],
        )

        # Install artwork
        self._install_artwork_for_shortcut(entry, shortcut, game.name)

    def _install_artwork_for_shortcut(
        self,
        entry: GameEntry,
        shortcut: Shortcut,
        game_title: str,
        system_id: str | None = None,
    ) -> None:
        """Install artwork for a synced shortcut.

        Checks cache first, then auto-fetches if enabled.

        Args:
            entry: The game entry
            shortcut: The Steam shortcut
            game_title: Title to search for artwork
            system_id: Optional system ID for ROM artwork
        """
        if not self._steam:
            return

        cache = ArtworkCache(self.config.pier_dir)

        # Check if we have cached artwork
        if cache.has_selected(entry.game_id):
            cached = cache.get_selected_artwork(entry.game_id)
            self._steam.install_artwork_from_cache(shortcut, cached)
            return

        # Auto-fetch if enabled
        if not self.config.auto_fetch_artwork:
            return

        # Fetch artwork
        artwork: ArtworkSet | None = None

        if system_id:
            # ROM - try libretro first, then SteamGridDB
            artwork = fetch_artwork_for_rom_sync(
                system_id,
                game_title,
                game_title,
                api_key=self.config.steamgriddb_api_key,
            )
        elif self.config.steamgriddb_api_key:
            # Port or custom game - use SteamGridDB
            from pier.core.artwork import fetch_artwork_for_port_sync

            artwork = fetch_artwork_for_port_sync(
                game_title,
                api_key=self.config.steamgriddb_api_key,
            )

        if artwork and artwork.grid:
            # Cache the artwork
            cache.cache_option(entry.game_id, "grid", 0, artwork.grid)
            cache.select_option(entry.game_id, "grid", 0)

            if artwork.hero:
                cache.cache_option(entry.game_id, "hero", 0, artwork.hero)
                cache.select_option(entry.game_id, "hero", 0)

            if artwork.logo:
                cache.cache_option(entry.game_id, "logo", 0, artwork.logo)
                cache.select_option(entry.game_id, "logo", 0)

            if artwork.icon:
                cache.cache_option(entry.game_id, "icon", 0, artwork.icon)
                cache.select_option(entry.game_id, "icon", 0)

            # Install to Steam
            cached = cache.get_selected_artwork(entry.game_id)
            self._steam.install_artwork_from_cache(shortcut, cached)

    def action_artwork(self) -> None:
        """Open artwork management dialog for selected game."""
        if not self._selected_entry:
            self.notify_warning("Select a game first")
            return

        if not self._steam:
            self.notify_error("Steam not found")
            return

        # Get the game title for searching
        entry = self._selected_entry
        if entry.game_id.startswith(GAME_ID_ROM_PREFIX):
            # ROM - use filename stem
            game_title = entry.name
        elif entry.game_id.startswith(GAME_ID_CUSTOM_PREFIX):
            # Custom game
            game_title = entry.name
        else:
            # Port - use the port's game name
            port = get_port(entry.game_id)
            game_title = port.game if port else entry.name

        from pier.tui.screens.artwork import ArtworkDialog

        steam = self._steam  # Capture for closure

        def on_result(saved: bool | None) -> None:
            if saved and steam:
                self.notify_success(f"Artwork updated for {entry.name}")
                # Re-install artwork to Steam if game is synced
                if entry.status == GameStatus.IN_STEAM:
                    shortcut = steam.find_shortcut(entry.name)
                    if shortcut:
                        cache = ArtworkCache(self.config.pier_dir)
                        cached = cache.get_selected_artwork(entry.game_id)
                        steam.install_artwork_from_cache(shortcut, cached)

        self.app.push_screen(
            ArtworkDialog(
                game_id=entry.game_id,
                game_title=game_title,
                config=self.config,
            ),
            on_result,
        )
