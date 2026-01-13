"""BIOS management screen."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Static

from pier.core.bios import BiosManager, BiosStatus
from pier.tui.screens.base import PierScreen


class BiosScreen(PierScreen):
    """Screen for managing BIOS files."""

    CSS = """
    BiosScreen {
        align: center middle;
    }

    #bios-container {
        width: 80%;
        height: 80%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    #bios-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        padding-bottom: 1;
    }

    #bios-table {
        height: 1fr;
        margin-bottom: 1;
    }

    #bios-actions {
        height: auto;
        align: center middle;
    }

    #bios-actions Button {
        margin: 0 1;
    }

    #bios-status {
        text-align: center;
        color: $text-muted;
        padding-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("r", "refresh", "Refresh"),
        Binding("d", "download_recommended", "Download Recommended"),
        Binding("a", "download_all", "Download All"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.manager = BiosManager()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static("BIOS Management", id="bios-title"),
            DataTable(id="bios-table"),
            Horizontal(
                Button("Refresh", id="btn-refresh", variant="default"),
                Button("Download Recommended", id="btn-download-rec", variant="primary"),
                Button("Download All", id="btn-download-all", variant="default"),
                Button("Back", id="btn-back", variant="default"),
                id="bios-actions",
            ),
            Static("", id="bios-status"),
            id="bios-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the table when mounted."""
        table = self.query_one("#bios-table", DataTable)
        table.add_columns("File", "System", "Status", "Priority", "Description")
        self.refresh_table()

    def refresh_table(self) -> None:
        """Refresh the BIOS status table."""
        table = self.query_one("#bios-table", DataTable)
        table.clear()

        results = self.manager.check_all()

        for result in results:
            status_text = self._format_status(result.status)
            priority = "Recommended" if result.bios.priority == 1 else "Optional"

            table.add_row(
                result.bios.filename,
                result.bios.system.upper(),
                status_text,
                priority,
                result.bios.description,
            )

        # Update summary
        valid = sum(1 for r in results if r.status == BiosStatus.VALID)
        invalid = sum(1 for r in results if r.status == BiosStatus.INVALID)
        missing = sum(1 for r in results if r.status == BiosStatus.MISSING)

        status_label = self.query_one("#bios-status", Static)
        status_label.update(
            f"Valid: {valid}  |  Invalid: {invalid}  |  Missing: {missing}  |  "
            f"BIOS dir: {self.manager.bios_dir}"
        )

    def _format_status(self, status: BiosStatus) -> str:
        """Format status for display."""
        if status == BiosStatus.VALID:
            return "✓ Valid"
        elif status == BiosStatus.INVALID:
            return "✗ Invalid"
        else:
            return "- Missing"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id
        if button_id == "btn-back":
            self.action_back()
        elif button_id == "btn-refresh":
            self.action_refresh()
        elif button_id == "btn-download-rec":
            self.action_download_recommended()
        elif button_id == "btn-download-all":
            self.action_download_all()

    def action_refresh(self) -> None:
        """Refresh the BIOS status."""
        self.refresh_table()
        self.notify_info("BIOS status refreshed")

    def action_download_recommended(self) -> None:
        """Download recommended BIOS files."""
        self.notify_info("Downloading recommended BIOS files...")
        self.run_worker(self._download_recommended(), exclusive=True)

    def action_download_all(self) -> None:
        """Download all BIOS files."""
        self.notify_info("Downloading all BIOS files...")
        self.run_worker(self._download_all(), exclusive=True)

    async def _download_recommended(self) -> None:
        """Worker to download recommended BIOS files."""
        try:
            paths = await self.manager.download_recommended()
            if paths:
                self.app.call_from_thread(self.notify_success, f"Downloaded {len(paths)} BIOS files")
            else:
                self.app.call_from_thread(
                    self.notify_info, "All recommended BIOS files already present"
                )
        except Exception as e:
            self.app.call_from_thread(self.notify_error, f"Error: {e}")
        finally:
            self.app.call_from_thread(self.refresh_table)

    async def _download_all(self) -> None:
        """Worker to download all BIOS files."""
        try:
            paths = await self.manager.download_all()
            if paths:
                self.app.call_from_thread(self.notify_success, f"Downloaded {len(paths)} BIOS files")
            else:
                self.app.call_from_thread(self.notify_info, "All BIOS files already present")
        except Exception as e:
            self.app.call_from_thread(self.notify_error, f"Error: {e}")
        finally:
            self.app.call_from_thread(self.refresh_table)
