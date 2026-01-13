"""Ports management screen."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Static

from pier.core.registry import get_port, list_ports
from pier.tui.screens.base import PierScreen


class PortsScreen(PierScreen):
    """Screen for managing native game ports."""

    DEFAULT_STATUS = "Select a port and press Install"

    CSS = """
    PortsScreen {
        align: center middle;
    }

    #ports-container {
        width: 90%;
        height: 90%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    #ports-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        padding-bottom: 1;
    }

    #ports-table {
        height: 1fr;
        margin-bottom: 1;
    }

    #ports-actions {
        height: auto;
        align: center middle;
    }

    #ports-actions Button {
        margin: 0 1;
    }

    #ports-info {
        text-align: center;
        color: $text-muted;
        padding-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("i", "install", "Install"),
        Binding("u", "update", "Update"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._selected_port_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static("Native Game Ports", id="ports-title"),
            DataTable(id="ports-table", cursor_type="row"),
            Horizontal(
                Button("Install", id="btn-install", variant="primary"),
                Button("Update", id="btn-update", variant="default"),
                Button("Refresh", id="btn-refresh", variant="default"),
                Button("Back", id="btn-back", variant="default"),
                id="ports-actions",
            ),
            Static(self.DEFAULT_STATUS, id="ports-info"),
            id="ports-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the table when mounted."""
        table = self.query_one("#ports-table", DataTable)
        table.add_columns("Port", "Game", "Status", "Version", "Steam", "HD Textures")
        self.refresh_table()

    def refresh_table(self) -> None:
        """Refresh the ports table."""
        # Reload library to get fresh data
        self.reload_library()

        table = self.query_one("#ports-table", DataTable)
        table.clear()

        for port in list_ports():
            installed_info = self.library.installed_ports.get(port.id, {})
            is_installed = port.id in self.library.installed_ports
            version = installed_info.get("version", "-")
            steam_linked = self.library.is_linked_to_steam(port.id)
            has_mods = len(port.mods) > 0

            status = "Installed" if is_installed else "Not installed"
            steam_status = "Yes" if steam_linked else "No" if is_installed else "-"
            mods_status = "Available" if has_mods else "-"

            table.add_row(
                port.name,
                port.game,
                status,
                version if is_installed else "-",
                steam_status,
                mods_status,
                key=port.id,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection."""
        self._selected_port_id = str(event.row_key.value) if event.row_key else None
        self._update_info()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle row highlight."""
        self._selected_port_id = str(event.row_key.value) if event.row_key else None
        self._update_info()

    def _update_info(self) -> None:
        """Update the info panel with selected port details."""
        info = self.query_one("#ports-info", Static)
        if self._selected_port_id:
            port = get_port(self._selected_port_id)
            if port:
                is_installed = self._selected_port_id in self.library.installed_ports
                if is_installed:
                    info.update(f"[bold]{port.name}[/bold]: {port.game} - Press U to update")
                else:
                    info.update(f"[bold]{port.name}[/bold]: {port.game} - Press I to install")
        else:
            info.update(self.DEFAULT_STATUS)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id
        if button_id == "btn-back":
            self.action_back()
        elif button_id == "btn-refresh":
            self.action_refresh()
        elif button_id == "btn-install":
            self.action_install()
        elif button_id == "btn-update":
            self.action_update()

    def action_refresh(self) -> None:
        """Refresh the table."""
        self.refresh_table()
        self.notify_info("Port list refreshed")

    def action_install(self) -> None:
        """Install the selected port."""
        if not self._selected_port_id:
            self.notify_warning("Select a port first")
            return

        port = get_port(self._selected_port_id)
        if not port:
            return

        if self._selected_port_id in self.library.installed_ports:
            self.notify_warning(f"{port.name} is already installed")
            return

        self.notify_info(f"Installing {port.name}... (this may take a while)")
        self.run_worker(self._install_port(self._selected_port_id), exclusive=True)

    def action_update(self) -> None:
        """Update the selected port."""
        if not self._selected_port_id:
            self.notify_warning("Select a port first")
            return

        port = get_port(self._selected_port_id)
        if not port:
            return

        if self._selected_port_id not in self.library.installed_ports:
            self.notify_warning(f"{port.name} is not installed")
            return

        self.notify_info(f"Checking for updates to {port.name}...")
        self.run_worker(self._update_port(self._selected_port_id), exclusive=True)

    async def _install_port(self, port_id: str) -> None:
        """Worker to install a port."""
        from pier.core.installer import InstallError, PortInstaller

        port = get_port(port_id)
        if not port:
            return

        try:
            installer = PortInstaller(config=self.config, library=self.library)
            await installer.install(
                port_id,
                with_mods=self.config.install_hd_textures,
                add_to_steam=self.config.auto_add_to_steam,
                fetch_artwork=self.config.auto_fetch_artwork,
            )
            self.call_from_thread(self.notify_success, f"Successfully installed {port.name}!")
        except InstallError as e:
            self.call_from_thread(self.notify_error, f"Installation failed: {e}")
        except Exception as e:
            self.call_from_thread(self.notify_error, f"Error: {e}")
        finally:
            self.call_from_thread(self.refresh_table)

    async def _update_port(self, port_id: str) -> None:
        """Worker to update a port."""
        from pier.core.installer import PortInstaller

        port = get_port(port_id)
        if not port:
            return

        try:
            installer = PortInstaller(config=self.config, library=self.library)
            update_info = await installer.check_update(port_id)

            if update_info:
                current, new = update_info
                self.call_from_thread(self.notify_info, f"Updating {port.name}: {current} -> {new}")
                await installer.update(port_id)
                self.call_from_thread(self.notify_success, f"Successfully updated {port.name}!")
            else:
                self.call_from_thread(self.notify_info, f"{port.name} is already up to date")
        except Exception as e:
            self.call_from_thread(self.notify_error, f"Update failed: {e}")
        finally:
            self.call_from_thread(self.refresh_table)
