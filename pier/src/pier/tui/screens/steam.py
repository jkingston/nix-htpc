"""Steam shortcuts management screen."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Static

from pier.core.constants import PIER_TAG, PORTS_TAG, STEAM_RUN_EXECUTABLE
from pier.core.registry import get_port
from pier.tui.screens.base import PierScreen


class SteamScreen(PierScreen):
    """Screen for managing Steam shortcuts."""

    CSS = """
    SteamScreen {
        align: center middle;
    }

    #steam-container {
        width: 80%;
        height: 80%;
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
        Binding("space", "toggle", "Toggle"),
        Binding("enter", "toggle", "Toggle"),
        Binding("s", "sync", "Sync to Steam"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._selected_port_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static("Steam Library", id="steam-title"),
            DataTable(id="steam-table", cursor_type="row"),
            Horizontal(
                Button("Toggle Link", id="btn-toggle", variant="primary"),
                Button("Sync to Steam", id="btn-sync", variant="default"),
                Button("Back", id="btn-back", variant="default"),
                id="steam-actions",
            ),
            Static("Toggle which installed games appear in Steam", id="steam-info"),
            id="steam-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the table when mounted."""
        table = self.query_one("#steam-table", DataTable)
        table.add_columns("Game", "Type", "Status", "Steam Linked")
        self.refresh_table()

    def refresh_table(self) -> None:
        """Refresh the Steam shortcuts table."""
        self.reload_library()

        table = self.query_one("#steam-table", DataTable)
        table.clear()

        # Add installed ports
        for port_id, _info in self.library.installed_ports.items():
            port = get_port(port_id)
            if port:
                linked = self.library.is_linked_to_steam(port_id)
                table.add_row(
                    port.name,
                    "Port",
                    "Installed",
                    "[green]Yes[/green]" if linked else "[dim]No[/dim]",
                    key=f"port:{port_id}",
                )

        # Add downloaded ROMs (could be linked to Steam via emulators)
        for system, roms in self.library.downloaded_roms.items():
            for rom in roms:
                rom_id = f"rom:{system}:{rom}"
                linked = self.library.is_linked_to_steam(rom_id)
                # Truncate long names
                display_name = rom[:40] + "..." if len(rom) > 43 else rom
                table.add_row(
                    display_name,
                    f"ROM ({system.upper()})",
                    "Downloaded",
                    "[green]Yes[/green]" if linked else "[dim]No[/dim]",
                    key=rom_id,
                )

        self._update_summary()

    def _update_summary(self) -> None:
        """Update the summary in the info label."""
        total_ports = len(self.library.installed_ports)
        linked_ports = sum(
            1
            for port_id in self.library.installed_ports
            if self.library.is_linked_to_steam(port_id)
        )

        info = self.query_one("#steam-info", Static)
        info.update(f"Ports: {linked_ports}/{total_ports} linked to Steam | Press Space to toggle")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection."""
        if event.row_key:
            self._selected_port_id = str(event.row_key.value)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle row highlight."""
        if event.row_key:
            self._selected_port_id = str(event.row_key.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id
        if button_id == "btn-back":
            self.action_back()
        elif button_id == "btn-toggle":
            self.action_toggle()
        elif button_id == "btn-sync":
            self.action_sync()

    def action_toggle(self) -> None:
        """Toggle Steam link for selected game."""
        if not self._selected_port_id:
            self.notify_warning("Select a game first")
            return

        # Parse the key
        if self._selected_port_id.startswith("port:"):
            game_id = self._selected_port_id[5:]
        else:
            game_id = self._selected_port_id

        # Toggle the link
        current = self.library.is_linked_to_steam(game_id)
        self.library.set_steam_link(game_id, not current)
        self.library.save(self.config.pier_dir)

        status = "linked to" if not current else "unlinked from"
        self.notify_info(f"Game {status} Steam")
        self.refresh_table()

    def action_sync(self) -> None:
        """Sync all linked games to Steam."""
        self.notify_info("Syncing shortcuts to Steam...")
        self.run_worker(self._sync_to_steam(), exclusive=True, thread=True)

    async def _sync_to_steam(self) -> None:
        """Worker to sync shortcuts to Steam."""
        from pier.core.steam import SteamLibrary

        try:
            steam = SteamLibrary()
            synced = 0

            for port_id, info in self.library.installed_ports.items():
                if self.library.is_linked_to_steam(port_id):
                    port = get_port(port_id)
                    if port:
                        exe_path = info.get("executable", "")
                        if exe_path:
                            steam.add_shortcut(
                                app_name=port.name,
                                exe=STEAM_RUN_EXECUTABLE,
                                start_dir=str(self.config.ports_dir / port_id),
                                launch_options=f'"{exe_path}"',
                                tags=[PIER_TAG, PORTS_TAG],
                            )
                            synced += 1

            self.app.call_from_thread(self.notify_success, f"Synced {synced} shortcuts to Steam")
        except FileNotFoundError:
            self.app.call_from_thread(
                self.notify_error,
                "Steam not found. Has Steam been run at least once?",
            )
        except Exception as e:
            self.app.call_from_thread(self.notify_error, f"Sync failed: {e}")
