"""Ports management screen."""

import time

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Static

from pier.core.registry import get_port, list_ports
from pier.tui.screens.base import PierScreen
from pier.tui.screens.progress import InstallProgress, InstallProgressScreen


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

        # Create progress tracker and modal
        progress = InstallProgress.for_port_install(port.name)
        progress_screen = InstallProgressScreen(progress)

        # Push modal and start install in a thread (needed for call_from_thread)
        self.app.push_screen(progress_screen)
        self.run_worker(
            self._install_port_with_progress(self._selected_port_id, progress_screen),
            exclusive=True,
            thread=True,
        )

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
        self.run_worker(self._update_port(self._selected_port_id), exclusive=True, thread=True)

    async def _install_port_with_progress(
        self, port_id: str, progress_screen: InstallProgressScreen
    ) -> None:
        """Worker to install a port with progress reporting."""
        from pier.core.installer import InstallError, PortInstaller, ProgressReporter

        port = get_port(port_id)
        if not port:
            return

        # Create callbacks that update the progress screen
        # Throttle progress updates to max 10/sec to avoid excessive cross-thread calls
        last_progress_update = 0.0

        def on_status(message: str) -> None:
            self.app.call_from_thread(progress_screen.update_status, message)

        def on_progress(downloaded: int, total: int) -> None:
            nonlocal last_progress_update
            now = time.time()
            # Only update UI max 10 times per second, or on completion
            if now - last_progress_update >= 0.1 or downloaded >= total:
                last_progress_update = now
                self.app.call_from_thread(progress_screen.update_progress, downloaded, total)

        progress = ProgressReporter(on_status=on_status, on_progress=on_progress)

        try:
            installer = PortInstaller(
                config=self.config, library=self.library, progress=progress
            )
            await installer.install(
                port_id,
                with_mods=self.config.install_hd_textures,
                add_to_steam=self.config.auto_add_to_steam,
                fetch_artwork=self.config.auto_fetch_artwork,
            )
            self.app.call_from_thread(
                progress_screen.mark_complete, True, f"Successfully installed {port.name}!"
            )
        except InstallError as e:
            self.app.call_from_thread(
                progress_screen.mark_complete, False, f"Installation failed: {e}"
            )
        except Exception as e:
            error_details = f"{type(e).__name__}: {e}"
            self.app.call_from_thread(
                progress_screen.mark_complete, False, f"Error: {error_details}"
            )
        finally:
            self.app.call_from_thread(self.refresh_table)

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
                self.app.call_from_thread(self.notify_info, f"Updating {port.name}: {current} -> {new}")
                await installer.update(port_id)
                self.app.call_from_thread(self.notify_success, f"Successfully updated {port.name}!")
            else:
                self.app.call_from_thread(self.notify_info, f"{port.name} is already up to date")
        except Exception as e:
            self.app.call_from_thread(self.notify_error, f"Update failed: {e}")
        finally:
            self.app.call_from_thread(self.refresh_table)
