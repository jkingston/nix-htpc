"""Base screen class with common functionality."""

from textual.binding import Binding
from textual.screen import Screen

from pier.core.config import Config, Library


class PierScreen(Screen):
    """Base screen class for pier TUI screens.

    Provides common functionality:
    - Config and library loading
    - Standard bindings (escape to go back)
    - Standard header/footer
    - Helper methods for common operations
    """

    # Default binding for going back - can be overridden by subclasses
    BINDINGS = [
        Binding("escape", "back", "Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._config: Config | None = None
        self._library: Library | None = None

    @property
    def config(self) -> Config:
        """Lazy-load configuration."""
        if self._config is None:
            self._config = Config.load()
        return self._config

    @property
    def library(self) -> Library:
        """Lazy-load library state."""
        if self._library is None:
            self._library = Library.load(self.config.pier_dir)
        return self._library

    def reload_library(self) -> None:
        """Reload library state from disk."""
        self._library = Library.load(self.config.pier_dir)

    def action_back(self) -> None:
        """Go back to the previous screen."""
        self.app.pop_screen()

    def notify_success(self, message: str) -> None:
        """Show a success notification."""
        self.notify(message)

    def notify_error(self, message: str) -> None:
        """Show an error notification."""
        self.notify(message, severity="error")

    def notify_warning(self, message: str) -> None:
        """Show a warning notification."""
        self.notify(message, severity="warning")

    def notify_info(self, message: str) -> None:
        """Show an info notification."""
        self.notify(message, severity="information")
