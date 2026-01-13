"""Configuration screen."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, Static

from pier.tui.screens.base import PierScreen


class ConfigScreen(PierScreen):
    """Screen for managing pier configuration."""

    CSS = """
    ConfigScreen {
        align: center middle;
    }

    #config-container {
        width: 70%;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    #config-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        padding-bottom: 1;
    }

    .config-section {
        margin-bottom: 1;
        padding: 1;
        background: $surface-darken-1;
    }

    .config-section-title {
        text-style: bold;
        margin-bottom: 1;
    }

    .config-row {
        height: auto;
        margin-bottom: 1;
    }

    .config-label {
        width: 25;
    }

    .config-value {
        width: 1fr;
    }

    #config-actions {
        height: auto;
        align: center middle;
        padding-top: 1;
    }

    #config-actions Button {
        margin: 0 1;
    }

    #config-paths {
        color: $text-muted;
        padding-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static("Configuration", id="config-title"),
            Vertical(
                Static("API Keys", classes="config-section-title"),
                Horizontal(
                    Label("SteamGridDB API Key:", classes="config-label"),
                    Input(
                        value=self.config.steamgriddb_api_key or "",
                        placeholder="Enter API key for artwork",
                        password=True,
                        id="input-steamgriddb-key",
                        classes="config-value",
                    ),
                    classes="config-row",
                ),
                classes="config-section",
            ),
            Vertical(
                Static("Installation Preferences", classes="config-section-title"),
                Checkbox(
                    "Auto-fetch artwork from SteamGridDB",
                    self.config.auto_fetch_artwork,
                    id="check-auto-artwork",
                ),
                Checkbox(
                    "Auto-add games to Steam library",
                    self.config.auto_add_to_steam,
                    id="check-auto-steam",
                ),
                Checkbox(
                    "Install HD texture packs when available",
                    self.config.install_hd_textures,
                    id="check-hd-textures",
                ),
                classes="config-section",
            ),
            Vertical(
                Static("Paths (read-only)", classes="config-section-title"),
                Static(f"Emulation dir: {self.config.emulation_dir}"),
                Static(f"ROMs dir: {self.config.roms_dir}"),
                Static(f"Ports dir: {self.config.ports_dir}"),
                Static(f"Config dir: {self.config.pier_dir}"),
                classes="config-section",
                id="config-paths",
            ),
            Horizontal(
                Button("Save", id="btn-save", variant="primary"),
                Button("Cancel", id="btn-cancel", variant="default"),
                id="config-actions",
            ),
            id="config-container",
        )
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id
        if button_id == "btn-cancel":
            self.action_back()
        elif button_id == "btn-save":
            self.action_save()

    def action_save(self) -> None:
        """Save configuration."""
        # Get values from inputs
        api_key_input = self.query_one("#input-steamgriddb-key", Input)
        auto_artwork = self.query_one("#check-auto-artwork", Checkbox)
        auto_steam = self.query_one("#check-auto-steam", Checkbox)
        hd_textures = self.query_one("#check-hd-textures", Checkbox)

        # Update config
        api_key = api_key_input.value.strip()
        self.config.steamgriddb_api_key = api_key if api_key else None
        self.config.auto_fetch_artwork = auto_artwork.value
        self.config.auto_add_to_steam = auto_steam.value
        self.config.install_hd_textures = hd_textures.value

        # Save to disk
        self.config.save()

        self.notify_success("Configuration saved")
        self.app.pop_screen()
