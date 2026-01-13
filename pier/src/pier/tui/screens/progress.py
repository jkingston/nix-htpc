"""Installation progress modal screen."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ProgressBar, RichLog, Static


class StepStatus(Enum):
    """Status of an installation step."""

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class InstallStep:
    """An installation step."""

    name: str
    description: str
    status: StepStatus = StepStatus.PENDING


@dataclass
class InstallProgress:
    """Tracks installation progress state."""

    title: str
    steps: list[InstallStep] = field(default_factory=list)
    current_step: int = 0
    progress: float = 0.0  # 0.0 to 1.0
    progress_text: str = ""
    log_lines: list[str] = field(default_factory=list)

    @classmethod
    def for_port_install(cls, port_name: str) -> "InstallProgress":
        """Create progress tracker for port installation."""
        return cls(
            title=f"Installing {port_name}",
            steps=[
                InstallStep("rom", "Verify/Download ROM"),
                InstallStep("download", "Download Port"),
                InstallStep("assets", "Generate Assets"),
                InstallStep("mods", "Install Mods"),
                InstallStep("steam", "Add to Steam"),
            ],
        )

    def log(self, message: str) -> None:
        """Add a log message with timestamp."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_lines.append(f"[dim]{timestamp}[/dim]  {message}")

    def set_step(self, step_name: str) -> None:
        """Set a step as active."""
        for i, step in enumerate(self.steps):
            if step.name == step_name:
                step.status = StepStatus.ACTIVE
                self.current_step = i
            elif step.status == StepStatus.ACTIVE:
                step.status = StepStatus.COMPLETE

    def complete_step(self, step_name: str) -> None:
        """Mark a step as complete."""
        for step in self.steps:
            if step.name == step_name:
                step.status = StepStatus.COMPLETE
                break

    def fail_step(self, step_name: str) -> None:
        """Mark a step as failed."""
        for step in self.steps:
            if step.name == step_name:
                step.status = StepStatus.FAILED
                break


class InstallProgressScreen(ModalScreen[bool]):
    """Modal screen showing installation progress."""

    CSS = """
    InstallProgressScreen {
        align: center middle;
    }

    #progress-container {
        width: 70;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    #progress-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        padding-bottom: 1;
    }

    #steps-container {
        height: auto;
        padding: 1 0;
    }

    .step-row {
        height: 1;
        padding: 0 1;
    }

    .step-pending {
        color: $text-muted;
    }

    .step-active {
        color: $warning;
        text-style: bold;
    }

    .step-complete {
        color: $success;
    }

    .step-failed {
        color: $error;
    }

    #progress-bar-container {
        height: 3;
        padding: 1 1;
    }

    #progress-text {
        text-align: center;
        height: 1;
        color: $text-muted;
    }

    #status-text {
        text-align: center;
        height: 1;
        padding-top: 1;
    }

    #log-container {
        height: 12;
        border: solid $primary-darken-2;
        margin-top: 1;
        display: none;
    }

    #log-container.visible {
        display: block;
    }

    #log-view {
        height: 100%;
    }

    #toggle-hint {
        text-align: center;
        color: $text-muted;
        padding-top: 1;
    }
    """

    BINDINGS = [
        Binding("l", "toggle_log", "Toggle Log"),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, progress: InstallProgress) -> None:
        super().__init__()
        self.progress = progress
        self._log_visible = False
        self._complete = False
        self._cancelled = False

    def compose(self) -> ComposeResult:
        with Container(id="progress-container"):
            yield Static(self.progress.title, id="progress-title")
            with Vertical(id="steps-container"):
                for step in self.progress.steps:
                    yield Static(
                        self._format_step(step),
                        classes=f"step-row step-{step.status.value}",
                        id=f"step-{step.name}",
                    )
            with Container(id="progress-bar-container"):
                yield ProgressBar(id="progress-bar", total=100, show_eta=False)
            yield Static("", id="progress-text")
            yield Static("Starting...", id="status-text")
            with Container(id="log-container"):
                yield RichLog(id="log-view", highlight=True, markup=True)
            yield Label("[dim]L[/dim] Toggle log  [dim]ESC[/dim] Cancel", id="toggle-hint")

    def _format_step(self, step: InstallStep) -> str:
        """Format a step for display."""
        icons = {
            StepStatus.PENDING: "[dim]○[/dim]",
            StepStatus.ACTIVE: "[yellow]→[/yellow]",
            StepStatus.COMPLETE: "[green]✓[/green]",
            StepStatus.FAILED: "[red]✗[/red]",
        }
        icon = icons[step.status]
        return f"  {icon} {step.description}"

    def update_steps(self) -> None:
        """Update step display."""
        if not self.is_mounted:
            return
        for step in self.progress.steps:
            widget = self.query_one(f"#step-{step.name}", Static)
            widget.update(self._format_step(step))
            widget.set_classes(f"step-row step-{step.status.value}")

    def update_progress(self, downloaded: int, total: int) -> None:
        """Update progress bar."""
        if not self.is_mounted:
            return
        if total > 0:
            pct = (downloaded / total) * 100
            self.progress.progress = downloaded / total

            # Format size
            def fmt_size(b: int) -> str:
                if b >= 1024 * 1024:
                    return f"{b / (1024 * 1024):.1f} MB"
                elif b >= 1024:
                    return f"{b / 1024:.1f} KB"
                return f"{b} B"

            self.progress.progress_text = f"{fmt_size(downloaded)} / {fmt_size(total)}"

            bar = self.query_one("#progress-bar", ProgressBar)
            bar.update(progress=pct)

            text = self.query_one("#progress-text", Static)
            text.update(self.progress.progress_text)

    def update_status(self, message: str) -> None:
        """Update status message and log."""
        self.progress.log(message)
        if not self.is_mounted:
            return

        status = self.query_one("#status-text", Static)
        status.update(message)

        log_view = self.query_one("#log-view", RichLog)
        log_view.write(self.progress.log_lines[-1])

        # Detect step changes from status messages
        msg_lower = message.lower()
        if "rom found" in msg_lower or "downloading rom" in msg_lower:
            self.progress.set_step("rom")
        elif "rom ready" in msg_lower or "rom copied" in msg_lower:
            self.progress.complete_step("rom")
        elif "fetching" in msg_lower and "release" in msg_lower or "downloading" in msg_lower and "rom" not in msg_lower:
            self.progress.set_step("download")
        elif "generating" in msg_lower or "extracting" in msg_lower and "torch" in msg_lower:
            self.progress.complete_step("download")
            self.progress.set_step("assets")
        elif "assets generated" in msg_lower or "assets extracted" in msg_lower:
            self.progress.complete_step("assets")
        elif "installing mod" in msg_lower:
            self.progress.set_step("mods")
        elif "installed:" in msg_lower and "mod" in msg_lower.split("installed")[0]:
            pass  # Still installing mods
        elif "fetching artwork" in msg_lower or "adding to steam" in msg_lower:
            self.progress.complete_step("mods")
            self.progress.set_step("steam")
        elif "steam shortcut" in msg_lower or "steam not found" in msg_lower:
            self.progress.complete_step("steam")
        elif "installation complete" in msg_lower:
            for step in self.progress.steps:
                if step.status != StepStatus.FAILED:
                    step.status = StepStatus.COMPLETE

        self.update_steps()

    def mark_complete(self, success: bool, message: str) -> None:
        """Mark installation as complete."""
        self._complete = True
        if not self.is_mounted:
            return

        status = self.query_one("#status-text", Static)
        if success:
            status.update(f"[green]{message}[/green]")
        else:
            status.update(f"[red]{message}[/red]")
            # Mark current step as failed
            for step in self.progress.steps:
                if step.status == StepStatus.ACTIVE:
                    step.status = StepStatus.FAILED
                    break
            self.update_steps()

        self.progress.log(message)
        log_view = self.query_one("#log-view", RichLog)
        log_view.write(self.progress.log_lines[-1])

        # Update hint
        hint = self.query_one("#toggle-hint", Label)
        hint.update("[dim]L[/dim] Toggle log  [dim]ESC[/dim] Close")

    def action_toggle_log(self) -> None:
        """Toggle log visibility."""
        self._log_visible = not self._log_visible
        log_container = self.query_one("#log-container")
        if self._log_visible:
            log_container.add_class("visible")
        else:
            log_container.remove_class("visible")

    def action_cancel(self) -> None:
        """Cancel or close."""
        if self._complete:
            self.dismiss(True)
        else:
            self._cancelled = True
            self.dismiss(False)

    @property
    def is_cancelled(self) -> bool:
        """Check if installation was cancelled."""
        return self._cancelled
