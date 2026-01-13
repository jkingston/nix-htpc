"""Main Textual application for pier."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Button, Footer, Header, Static

from pier.core.config import Config, Library


class MenuItem(Button):
    """A menu item button."""

    def __init__(self, label: str, key: str, description: str, action: str) -> None:
        super().__init__(f"[{key}] {label}", id=f"menu-{action}")
        self.action_name = action
        self.description = description


class HomeScreen(Container):
    """Home screen with main menu."""

    def compose(self) -> ComposeResult:
        yield Static("pier", id="logo", classes="title")
        yield Static("HTPC Game Management", classes="subtitle")
        yield Container(
            MenuItem("Ports", "P", "Manage native game ports", "ports"),
            MenuItem("ROMs", "R", "Browse & download ROMs", "roms"),
            MenuItem("BIOS", "B", "Check & download BIOS files", "bios"),
            MenuItem("Steam", "S", "Manage Steam shortcuts", "steam"),
            MenuItem("Update", "U", "Check for updates", "update"),
            MenuItem("Config", "C", "Settings", "config"),
            MenuItem("Quit", "Q", "Exit pier", "quit"),
            id="menu",
        )


class PierApp(App):
    """Main pier TUI application."""

    CSS = """
    Screen {
        align: center middle;
    }

    #logo {
        text-align: center;
        text-style: bold;
        color: $accent;
        padding: 1 0;
    }

    .title {
        text-align: center;
        text-style: bold;
        width: 100%;
    }

    .subtitle {
        text-align: center;
        color: $text-muted;
        width: 100%;
        padding-bottom: 1;
    }

    #menu {
        width: auto;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $primary;
    }

    #menu Button {
        width: 100%;
        margin: 0 0 1 0;
    }

    #menu Button:last-child {
        margin-bottom: 0;
    }

    MenuItem {
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("p", "show_ports", "Ports"),
        Binding("r", "show_roms", "ROMs"),
        Binding("b", "show_bios", "BIOS"),
        Binding("s", "show_steam", "Steam"),
        Binding("u", "check_updates", "Update"),
        Binding("c", "show_config", "Config"),
        Binding("q", "quit", "Quit"),
    ]

    TITLE = "pier"
    SUB_TITLE = "HTPC Game Management"

    def __init__(self) -> None:
        super().__init__()
        self.config = Config.load()
        self.library = Library.load(self.config.pier_dir)

    def compose(self) -> ComposeResult:
        yield Header()
        yield HomeScreen()
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle menu button presses."""
        button = event.button
        if isinstance(button, MenuItem):
            action = button.action_name
            if action == "quit":
                self.exit()
            elif action == "ports":
                self.action_show_ports()
            elif action == "roms":
                self.action_show_roms()
            elif action == "bios":
                self.action_show_bios()
            elif action == "steam":
                self.action_show_steam()
            elif action == "update":
                self.action_check_updates()
            elif action == "config":
                self.action_show_config()

    def action_show_ports(self) -> None:
        """Show ports management screen."""
        from pier.tui.screens.ports import PortsScreen

        self.push_screen(PortsScreen())

    def action_show_roms(self) -> None:
        """Show ROM browser screen."""
        from pier.tui.screens.roms import RomsScreen

        self.push_screen(RomsScreen())

    def action_show_bios(self) -> None:
        """Show BIOS management screen."""
        from pier.tui.screens.bios import BiosScreen

        self.push_screen(BiosScreen())

    def action_show_steam(self) -> None:
        """Show Steam management screen."""
        from pier.tui.screens.steam import SteamScreen

        self.push_screen(SteamScreen())

    def action_check_updates(self) -> None:
        """Check for updates."""
        self.notify("Checking for updates...")
        self.run_worker(self._check_updates(), exclusive=True)

    async def _check_updates(self) -> None:
        """Worker to check for updates."""
        from pier.core.installer import PortInstaller

        try:
            installer = PortInstaller(config=self.config, library=self.library)
            updates = []

            for port_id in self.library.installed_ports:
                update_info = await installer.check_update(port_id)
                if update_info:
                    updates.append((port_id, update_info[0], update_info[1]))

            if updates:
                msg = f"Updates available for: {', '.join(u[0] for u in updates)}"
                self.call_from_thread(self.notify, msg)
            else:
                self.call_from_thread(self.notify, "All ports are up to date")
        except Exception as e:
            self.call_from_thread(self.notify, f"Error: {e}", severity="error")

    def action_show_config(self) -> None:
        """Show configuration screen."""
        from pier.tui.screens.config import ConfigScreen

        self.push_screen(ConfigScreen())


if __name__ == "__main__":
    app = PierApp()
    app.run()
