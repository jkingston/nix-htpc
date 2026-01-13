"""ROM browser screen."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Input, Select, Static

from pier.core.registry import SYSTEMS, list_systems
from pier.core.myrient import MyrientBrowser, MyrientEntry
from pier.tui.screens.base import PierScreen


class RomsScreen(PierScreen):
    """Screen for browsing and downloading ROMs."""

    CSS = """
    RomsScreen {
        align: center middle;
    }

    #roms-container {
        width: 95%;
        height: 95%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    #roms-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        padding-bottom: 1;
    }

    #roms-controls {
        height: auto;
        margin-bottom: 1;
    }

    #system-select {
        width: 30;
        margin-right: 2;
    }

    #search-input {
        width: 1fr;
    }

    #roms-table {
        height: 1fr;
        margin-bottom: 1;
    }

    #roms-actions {
        height: auto;
        align: center middle;
    }

    #roms-actions Button {
        margin: 0 1;
    }

    #roms-status {
        text-align: center;
        color: $text-muted;
        padding-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("d", "download", "Download"),
        Binding("r", "refresh", "Refresh"),
        Binding("ctrl+f", "focus_search", "Search"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._current_system: str = "n64"
        self._entries: list[MyrientEntry] = []
        self._filtered_entries: list[MyrientEntry] = []
        self._selected_entry: MyrientEntry | None = None
        self._loading = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static("ROM Browser", id="roms-title"),
            Horizontal(
                Select(
                    [(s.name, s.id) for s in list_systems()],
                    value="n64",
                    id="system-select",
                    allow_blank=False,
                ),
                Input(placeholder="Search ROMs...", id="search-input"),
                id="roms-controls",
            ),
            DataTable(id="roms-table", cursor_type="row"),
            Horizontal(
                Button("Download", id="btn-download", variant="primary"),
                Button("Refresh", id="btn-refresh", variant="default"),
                Button("Back", id="btn-back", variant="default"),
                id="roms-actions",
            ),
            Static("Select a system to browse ROMs", id="roms-status"),
            id="roms-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the table when mounted."""
        table = self.query_one("#roms-table", DataTable)
        table.add_columns("Name", "Size", "Downloaded")
        # Start loading ROMs for default system
        self._load_roms()

    def _load_roms(self) -> None:
        """Load ROMs for the current system."""
        if self._loading:
            return
        self._loading = True
        self._update_status(f"Loading {SYSTEMS[self._current_system].name} ROMs...")
        self.run_worker(self._fetch_roms(), exclusive=True)

    async def _fetch_roms(self) -> None:
        """Worker to fetch ROMs from myrient."""
        browser = MyrientBrowser()
        try:
            entries = await browser.list_system(self._current_system)
            # Filter to only files (not directories)
            self._entries = [e for e in entries if not e.is_directory]
            self._filtered_entries = self._entries
            self.call_from_thread(self._populate_table)
            self.call_from_thread(
                self._update_status,
                f"Found {len(self._entries)} ROMs for {SYSTEMS[self._current_system].name}"
            )
        except Exception as e:
            self.call_from_thread(self.notify_error, f"Error loading ROMs: {e}")
            self.call_from_thread(self._update_status, f"Error: {e}")
        finally:
            await browser.close()
            self._loading = False

    def _populate_table(self) -> None:
        """Populate the table with filtered entries."""
        table = self.query_one("#roms-table", DataTable)
        table.clear()

        # Get downloaded ROMs for this system
        downloaded = set(self.library.downloaded_roms.get(self._current_system, []))

        for entry in self._filtered_entries[:200]:  # Limit to 200 for performance
            is_downloaded = entry.name in downloaded or any(
                entry.name.replace(".zip", ext) in downloaded
                for ext in SYSTEMS[self._current_system].extensions
            )
            status = "Yes" if is_downloaded else "-"
            table.add_row(entry.name, entry.size, status, key=entry.path)

        if len(self._filtered_entries) > 200:
            self._update_status(
                f"Showing 200 of {len(self._filtered_entries)} ROMs - use search to filter"
            )

    def _update_status(self, message: str) -> None:
        """Update the status label."""
        status = self.query_one("#roms-status", Static)
        status.update(message)

    def _filter_entries(self, query: str) -> None:
        """Filter entries based on search query."""
        if not query:
            self._filtered_entries = self._entries
        else:
            query_lower = query.lower()
            self._filtered_entries = [
                e for e in self._entries
                if query_lower in e.name.lower()
            ]
        self._populate_table()
        if query:
            self._update_status(f"Found {len(self._filtered_entries)} matching ROMs")

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle system selection change."""
        if event.select.id == "system-select" and event.value:
            self._current_system = str(event.value)
            # Clear search
            search_input = self.query_one("#search-input", Input)
            search_input.value = ""
            self._load_roms()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle search input change."""
        if event.input.id == "search-input":
            self._filter_entries(event.value)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection."""
        if event.row_key:
            path = str(event.row_key.value)
            self._selected_entry = next(
                (e for e in self._filtered_entries if e.path == path), None
            )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle row highlight."""
        if event.row_key:
            path = str(event.row_key.value)
            self._selected_entry = next(
                (e for e in self._filtered_entries if e.path == path), None
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id
        if button_id == "btn-back":
            self.action_back()
        elif button_id == "btn-refresh":
            self.action_refresh()
        elif button_id == "btn-download":
            self.action_download()

    def action_refresh(self) -> None:
        """Refresh the ROM list."""
        self._load_roms()

    def action_focus_search(self) -> None:
        """Focus the search input."""
        self.query_one("#search-input", Input).focus()

    def action_download(self) -> None:
        """Download the selected ROM."""
        if not self._selected_entry:
            self.notify_warning("Select a ROM first")
            return

        self.notify_info(f"Downloading {self._selected_entry.name}...")
        self.run_worker(
            self._download_rom(self._selected_entry),
            exclusive=True,
        )

    async def _download_rom(self, entry: MyrientEntry) -> None:
        """Worker to download a ROM."""
        browser = MyrientBrowser()
        try:
            dest_dir = self.config.roms_dir / self._current_system
            path = await browser.download(
                entry.path,
                dest_dir,
                extract=True,
            )
            # Record in library
            self.library.add_rom(self._current_system, path.name)
            self.library.save(self.config.pier_dir)

            self.call_from_thread(self.notify_success, f"Downloaded: {path.name}")
            self.call_from_thread(self._populate_table)
        except Exception as e:
            self.call_from_thread(self.notify_error, f"Download failed: {e}")
        finally:
            await browser.close()
