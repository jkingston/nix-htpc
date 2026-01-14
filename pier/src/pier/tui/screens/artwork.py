"""Artwork selection dialog."""

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Static

from pier.core.artwork import SteamGridDB
from pier.core.artwork_cache import ArtworkCache, ArtworkOptionMeta
from pier.core.config import Config

# Try to import textual-image, fall back gracefully if not available
try:
    from textual_image.widget import Image as ImageWidget

    HAS_TEXTUAL_IMAGE = True
except ImportError:
    HAS_TEXTUAL_IMAGE = False
    ImageWidget = None  # type: ignore


class ArtworkThumbnail(Container):
    """A selectable thumbnail container."""

    DEFAULT_CSS = """
    ArtworkThumbnail {
        width: auto;
        height: auto;
        padding: 0 1;
        border: solid $surface;
    }

    ArtworkThumbnail.selected {
        border: solid $primary;
    }

    ArtworkThumbnail .thumb-index {
        text-align: center;
        width: 100%;
    }

    ArtworkThumbnail .thumb-selected {
        text-align: center;
        color: $success;
    }
    """

    def __init__(
        self,
        index: int,
        image_path: Path | None = None,
        is_selected: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.index = index
        self.image_path = image_path
        self.is_selected = is_selected
        if is_selected:
            self.add_class("selected")

    def compose(self) -> ComposeResult:
        # Show image if available and textual-image is installed
        if self.image_path and self.image_path.exists() and HAS_TEXTUAL_IMAGE and ImageWidget:
            yield ImageWidget(str(self.image_path))
        else:
            # Placeholder for missing image
            yield Static(f"[{self.index + 1}]", classes="thumb-index")

        # Selection indicator
        if self.is_selected:
            yield Static("*", classes="thumb-selected")
        else:
            yield Static(" ", classes="thumb-selected")


class ArtworkSection(Container):
    """A section for one artwork type (grid, hero, logo, icon)."""

    DEFAULT_CSS = """
    ArtworkSection {
        height: auto;
        width: 100%;
        padding: 1;
        border: solid $surface;
        margin-bottom: 1;
    }

    ArtworkSection.focused {
        border: solid $primary;
    }

    ArtworkSection .section-title {
        text-style: bold;
        margin-bottom: 1;
    }

    ArtworkSection .thumbnails {
        height: auto;
        width: 100%;
    }

    ArtworkSection .no-options {
        color: $text-muted;
        text-style: italic;
    }
    """

    def __init__(self, art_type: str, title: str, **kwargs):
        super().__init__(**kwargs)
        self.art_type = art_type
        self.title = title
        self._options: list[Path] = []
        self._selected_index: int | None = None
        self._current_index: int = 0

    def compose(self) -> ComposeResult:
        yield Static(self.title, classes="section-title")
        yield Horizontal(id=f"thumbnails-{self.art_type}", classes="thumbnails")

    def update_options(self, options: list[Path], selected_index: int | None) -> None:
        """Update the displayed options."""
        self._options = options
        self._selected_index = selected_index
        self._current_index = selected_index if selected_index is not None else 0

        container = self.query_one(f"#thumbnails-{self.art_type}", Horizontal)
        container.remove_children()

        if not options:
            container.mount(Static("No artwork available", classes="no-options"))
        else:
            for i, path in enumerate(options):
                thumb = ArtworkThumbnail(
                    index=i,
                    image_path=path,
                    is_selected=(i == selected_index),
                    id=f"thumb-{self.art_type}-{i}",
                )
                container.mount(thumb)

    def select_current(self) -> int | None:
        """Select the currently highlighted option."""
        if self._options and 0 <= self._current_index < len(self._options):
            self._selected_index = self._current_index
            self.update_options(self._options, self._selected_index)
            return self._current_index
        return None

    def move_cursor(self, delta: int) -> None:
        """Move the cursor left/right."""
        if not self._options:
            return

        old_index = self._current_index
        self._current_index = max(0, min(len(self._options) - 1, self._current_index + delta))

        if old_index != self._current_index:
            # Update visual highlight
            old_thumb = self.query_one(f"#thumb-{self.art_type}-{old_index}", ArtworkThumbnail)
            new_thumb = self.query_one(f"#thumb-{self.art_type}-{self._current_index}", ArtworkThumbnail)
            old_thumb.remove_class("selected")
            if old_index != self._selected_index:
                old_thumb.remove_class("selected")
            new_thumb.add_class("selected")


class ArtworkDialog(ModalScreen[bool]):
    """Modal dialog for browsing and selecting artwork."""

    DEFAULT_CSS = """
    ArtworkDialog {
        align: center middle;
    }

    #artwork-container {
        width: 90%;
        height: 90%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    #artwork-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        padding-bottom: 1;
    }

    #artwork-sections {
        height: 1fr;
        width: 100%;
    }

    #artwork-info {
        text-align: center;
        color: $text-muted;
        padding: 1 0;
    }

    #artwork-actions {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    #artwork-actions Button {
        margin: 0 1;
    }

    #artwork-status {
        text-align: center;
        padding-top: 1;
    }
    """

    BINDINGS = [
        Binding("tab", "next_section", "Next Section"),
        Binding("shift+tab", "prev_section", "Prev Section"),
        Binding("left", "prev_option", "Previous"),
        Binding("right", "next_option", "Next"),
        Binding("f", "fetch", "Fetch"),
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "select", "Select"),
        Binding("s", "save", "Save"),
    ]

    def __init__(self, game_id: str, game_title: str, config: Config):
        super().__init__()
        self.game_id = game_id
        self.game_title = game_title
        self.config = config
        self._cache = ArtworkCache(config.pier_dir)
        self._current_section = 0
        self._sections: list[ArtworkSection] = []
        self._modified = False

    def compose(self) -> ComposeResult:
        yield Container(
            Static(f"Artwork: {self.game_title}", id="artwork-title"),
            ScrollableContainer(
                ArtworkSection("grid", "Grid (600x900)", id="section-grid"),
                ArtworkSection("hero", "Hero (1920x620)", id="section-hero"),
                ArtworkSection("logo", "Logo", id="section-logo"),
                ArtworkSection("icon", "Icon", id="section-icon"),
                id="artwork-sections",
            ),
            Static("", id="artwork-info"),
            Horizontal(
                Button("Fetch More", id="btn-fetch", variant="default"),
                Button("Select", id="btn-select", variant="default"),
                Button("Save & Apply", id="btn-save", variant="primary"),
                Button("Cancel", id="btn-cancel", variant="default"),
                id="artwork-actions",
            ),
            Static("", id="artwork-status"),
            id="artwork-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Load cached artwork on mount."""
        self._sections = [
            self.query_one("#section-grid", ArtworkSection),
            self.query_one("#section-hero", ArtworkSection),
            self.query_one("#section-logo", ArtworkSection),
            self.query_one("#section-icon", ArtworkSection),
        ]

        # Load cached options for each type
        for section in self._sections:
            options = self._cache.get_options(self.game_id, section.art_type)
            selected = self._cache.get_selected_index(self.game_id, section.art_type)
            section.update_options(options, selected)

        # Focus first section
        self._update_section_focus()
        self._update_info()

    def _update_section_focus(self) -> None:
        """Update visual focus indicator on sections."""
        for i, section in enumerate(self._sections):
            if i == self._current_section:
                section.add_class("focused")
            else:
                section.remove_class("focused")

    def _update_info(self) -> None:
        """Update the info text with current selection details."""
        section = self._sections[self._current_section]
        art_type = section.art_type

        meta = self._cache.get_option_meta(self.game_id, art_type, section._current_index)
        if meta:
            info = f"Option {meta.index + 1}"
            if meta.author:
                info += f" by {meta.author}"
            if meta.score:
                info += f" | Score: {meta.score}"
            if meta.style:
                info += f" | Style: {meta.style}"
        else:
            info = f"Viewing {art_type} options"

        self.query_one("#artwork-info", Static).update(info)

    def _update_status(self, message: str) -> None:
        """Update status message."""
        self.query_one("#artwork-status", Static).update(message)

    def action_next_section(self) -> None:
        """Move to next section."""
        self._current_section = (self._current_section + 1) % len(self._sections)
        self._update_section_focus()
        self._update_info()

    def action_prev_section(self) -> None:
        """Move to previous section."""
        self._current_section = (self._current_section - 1) % len(self._sections)
        self._update_section_focus()
        self._update_info()

    def action_next_option(self) -> None:
        """Move to next option in current section."""
        self._sections[self._current_section].move_cursor(1)
        self._update_info()

    def action_prev_option(self) -> None:
        """Move to previous option in current section."""
        self._sections[self._current_section].move_cursor(-1)
        self._update_info()

    def action_select(self) -> None:
        """Select current option."""
        section = self._sections[self._current_section]
        index = section.select_current()
        if index is not None:
            self._cache.select_option(self.game_id, section.art_type, index)
            self._modified = True
            self._update_status(f"Selected {section.art_type} option {index + 1}")

    def action_fetch(self) -> None:
        """Fetch more artwork options from SteamGridDB."""
        if not self.config.steamgriddb_api_key:
            self._update_status("No SteamGridDB API key configured")
            return

        self._update_status("Fetching artwork...")
        self.run_worker(self._fetch_artwork(), exclusive=True, thread=True)

    async def _fetch_artwork(self) -> None:
        """Worker to fetch artwork from SteamGridDB."""
        api_key = self.config.steamgriddb_api_key
        if not api_key:
            self.app.call_from_thread(self._update_status, "No API key configured")
            return

        try:
            sgdb = SteamGridDB(api_key)
            try:
                game_id, options = await sgdb.fetch_all_options(self.game_title, limit=10)

                if not game_id:
                    self.app.call_from_thread(self._update_status, "Game not found on SteamGridDB")
                    return

                # Store the SteamGridDB game ID
                self._cache.set_steamgriddb_id(self.game_id, game_id)

                # Download and cache each option
                fetched = 0
                for art_type, art_options in options.items():
                    for opt in art_options:
                        image_bytes = await sgdb.download_option(opt)
                        if image_bytes:
                            meta = ArtworkOptionMeta(
                                index=opt.index,
                                url=opt.url,
                                author=opt.author,
                                score=opt.score,
                                style=opt.style,
                            )
                            self._cache.cache_option(self.game_id, art_type, opt.index, image_bytes, meta)
                            fetched += 1

                # Refresh display
                self.app.call_from_thread(self._refresh_sections)
                self.app.call_from_thread(self._update_status, f"Fetched {fetched} artwork images")

            finally:
                await sgdb.close()

        except Exception as e:
            self.app.call_from_thread(self._update_status, f"Fetch failed: {e}")

    def _refresh_sections(self) -> None:
        """Refresh all sections with cached data."""
        for section in self._sections:
            options = self._cache.get_options(self.game_id, section.art_type)
            selected = self._cache.get_selected_index(self.game_id, section.art_type)
            section.update_options(options, selected)

    def action_save(self) -> None:
        """Save and apply changes."""
        self.dismiss(self._modified)

    def action_cancel(self) -> None:
        """Cancel without saving."""
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id
        if button_id == "btn-fetch":
            self.action_fetch()
        elif button_id == "btn-select":
            self.action_select()
        elif button_id == "btn-save":
            self.action_save()
        elif button_id == "btn-cancel":
            self.action_cancel()
