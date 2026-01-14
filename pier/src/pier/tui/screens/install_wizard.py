"""Install wizard screen for GOG/itch.io games."""

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from pier.core.config import CustomGame
from pier.core.installer import (
    InstallerType,
    UnsupportedInstallerError,
    detect_installer_type,
    find_executables,
    get_installer_description,
    run_installer,
)
from pier.tui.screens.base import PierScreen


class SelectExecutableDialog(ModalScreen[Path | None]):
    """Modal dialog to select the main executable."""

    CSS = """
    SelectExecutableDialog {
        align: center middle;
    }

    #dialog-container {
        width: 70;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    #dialog-title {
        text-align: center;
        text-style: bold;
        padding-bottom: 1;
    }

    #exe-select {
        width: 100%;
        margin-bottom: 1;
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

    def __init__(self, executables: list[Path]):
        super().__init__()
        self._executables = executables
        self._selected: Path | None = executables[0] if executables else None

    def compose(self) -> ComposeResult:
        options = [(str(exe), str(exe)) for exe in self._executables]
        yield Container(
            Static("Select Main Executable", id="dialog-title"),
            Static("Multiple executables found. Select the main game executable:"),
            Select(options, value=str(self._selected) if self._selected else None, id="exe-select"),
            Horizontal(
                Button("Select", id="btn-select", variant="primary"),
                Button("Cancel", id="btn-cancel", variant="default"),
                id="dialog-actions",
            ),
            id="dialog-container",
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.value:
            self._selected = Path(str(event.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
        elif event.button.id == "btn-select":
            self.dismiss(self._selected)

    def action_cancel(self) -> None:
        self.dismiss(None)


class InstallWizardScreen(PierScreen):
    """Screen for installing games from GOG/itch.io installers."""

    CSS = """
    InstallWizardScreen {
        align: center middle;
    }

    #wizard-container {
        width: 80;
        height: auto;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    #wizard-title {
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

    #installer-status {
        text-align: center;
        padding: 1 0;
    }

    #wizard-actions {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    #wizard-actions Button {
        margin: 0 1;
    }

    #wizard-info {
        text-align: center;
        color: $text-muted;
        padding-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "back", "Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._installer_path: Path | None = None
        self._installer_type: InstallerType = InstallerType.UNSUPPORTED
        self._installing = False

    def compose(self) -> ComposeResult:
        yield Container(
            Static("Install Game", id="wizard-title"),
            Horizontal(
                Label("Installer:", classes="field-label"),
                Input(placeholder="/path/to/setup.exe", id="input-installer", classes="field-input"),
                classes="field-row",
            ),
            Horizontal(
                Label("Game Name:", classes="field-label"),
                Input(placeholder="Game name", id="input-name", classes="field-input"),
                classes="field-row",
            ),
            Horizontal(
                Label("Install To:", classes="field-label"),
                Input(placeholder="~/Games/GameName", id="input-dest", classes="field-input"),
                classes="field-row",
            ),
            Static("", id="installer-status"),
            Horizontal(
                Button("Install", id="btn-install", variant="primary"),
                Button("Back", id="btn-back", variant="default"),
                id="wizard-actions",
            ),
            Static("Enter path to installer file (.exe or .sh)", id="wizard-info"),
            id="wizard-container",
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "input-installer":
            self._check_installer(event.value)
        elif event.input.id == "input-name":
            # Auto-update destination when name changes
            name = event.value.strip()
            if name:
                dest_input = self.query_one("#input-dest", Input)
                if not dest_input.value or dest_input.value.startswith(str(Path.home() / "Games")):
                    safe_name = name.replace(" ", "_").replace("/", "_")
                    dest_input.value = str(Path.home() / "Games" / safe_name)

    def _check_installer(self, path_str: str) -> None:
        """Check if the installer path is valid and detect type."""
        status = self.query_one("#installer-status", Static)

        if not path_str:
            status.update("")
            self._installer_path = None
            self._installer_type = InstallerType.UNSUPPORTED
            return

        path = Path(path_str).expanduser()
        if not path.exists():
            status.update("[yellow]File not found[/yellow]")
            self._installer_path = None
            self._installer_type = InstallerType.UNSUPPORTED
            return

        self._installer_path = path
        self._installer_type = detect_installer_type(path)

        if self._installer_type == InstallerType.UNSUPPORTED:
            status.update("[red]Unsupported installer format - install manually[/red]")
        else:
            desc = get_installer_description(self._installer_type)
            status.update(f"[green]Detected: {desc} (silent install supported)[/green]")

            # Auto-fill name from filename if empty
            name_input = self.query_one("#input-name", Input)
            if not name_input.value:
                # Try to extract game name from installer filename
                name = path.stem
                for prefix in ("setup_", "Setup_", "install_", "Install_"):
                    if name.startswith(prefix):
                        name = name[len(prefix) :]
                # Remove version numbers at end
                parts = name.rsplit("_", 1)
                if len(parts) > 1 and parts[1].replace(".", "").isdigit():
                    name = parts[0]
                name_input.value = name.replace("_", " ").title()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-back":
            self.action_back()
        elif event.button.id == "btn-install":
            self._start_install()

    def _start_install(self) -> None:
        """Start the installation process."""
        if self._installing:
            return

        # Validate inputs
        if not self._installer_path or not self._installer_path.exists():
            self.notify_error("Please enter a valid installer path")
            return

        if self._installer_type == InstallerType.UNSUPPORTED:
            self.notify_error("This installer format is not supported for silent install")
            return

        name = self.query_one("#input-name", Input).value.strip()
        if not name:
            self.notify_error("Please enter a game name")
            return

        dest_str = self.query_one("#input-dest", Input).value.strip()
        if not dest_str:
            self.notify_error("Please enter a destination path")
            return

        dest = Path(dest_str).expanduser()

        self._installing = True
        self.notify_info("Installing...")
        self.run_worker(
            self._do_install(self._installer_path, dest, name),
            exclusive=True,
            thread=True,
        )

    async def _do_install(self, installer: Path, dest: Path, name: str) -> None:
        """Worker to run the installation."""
        try:

            def on_status(msg: str) -> None:
                self.app.call_from_thread(self._update_status, msg)

            install_dir = await run_installer(installer, dest, name, on_status)

            # Find executables
            executables = find_executables(install_dir)
            if not executables:
                self.app.call_from_thread(
                    self.notify_error, "No executables found in installed directory"
                )
                return

            # If multiple executables, let user choose
            if len(executables) > 1:
                self.app.call_from_thread(self._select_executable, executables, name, install_dir)
            else:
                self.app.call_from_thread(
                    self._finish_install, executables[0], name, install_dir
                )

        except UnsupportedInstallerError as e:
            self.app.call_from_thread(self.notify_error, str(e))
        except Exception as e:
            self.app.call_from_thread(self.notify_error, f"Installation failed: {e}")
        finally:
            self._installing = False

    def _update_status(self, msg: str) -> None:
        """Update the status display."""
        status = self.query_one("#installer-status", Static)
        status.update(f"[cyan]{msg}[/cyan]")

    def _select_executable(self, executables: list[Path], name: str, install_dir: Path) -> None:
        """Show dialog to select the main executable."""

        def on_result(selected: Path | None) -> None:
            if selected:
                self._finish_install(selected, name, install_dir)

        self.app.push_screen(SelectExecutableDialog(executables), on_result)

    def _finish_install(self, executable: Path, name: str, install_dir: Path) -> None:
        """Finish installation by adding to library."""
        # Determine if this is a Windows exe
        use_steam_run = executable.suffix.lower() == ".exe"

        # Add to library
        game_id = f"custom:{name.lower().replace(' ', '_')}"
        game = CustomGame(
            name=name,
            executable=str(executable),
            start_dir=str(install_dir),
            launch_args="",
            use_steam_run=use_steam_run,
        )

        self.library.add_custom_game(game_id, game)
        self.library.save(self.config.pier_dir)

        self.notify_success(f"Installed {name}")
        self._update_status("[green]Installed! Use Steam Sync to add to Steam.[/green]")
