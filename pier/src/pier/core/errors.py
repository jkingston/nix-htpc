"""Structured error types for pier."""


class PierError(Exception):
    """Base exception for all pier errors."""

    def __init__(self, message: str, details: str | None = None):
        self.message = message
        self.details = details
        super().__init__(message)

    def __str__(self) -> str:
        if self.details:
            return f"{self.message}: {self.details}"
        return self.message


# =============================================================================
# Installation Errors
# =============================================================================


class InstallError(PierError):
    """Base exception for installation failures."""

    pass


class ROMNotFoundError(InstallError):
    """Required ROM file was not found."""

    def __init__(self, rom_name: str, system: str):
        super().__init__(
            f"ROM not found: {rom_name}",
            f"System: {system}. Download from Myrient or provide the file manually.",
        )
        self.rom_name = rom_name
        self.system = system


class ROMHashMismatchError(InstallError):
    """ROM file hash does not match expected value."""

    def __init__(self, rom_name: str, expected: str, actual: str):
        super().__init__(
            f"ROM hash mismatch: {rom_name}",
            f"Expected: {expected}, got: {actual}",
        )
        self.rom_name = rom_name
        self.expected = expected
        self.actual = actual


class AssetGenerationError(InstallError):
    """Failed to generate game assets from ROM."""

    def __init__(self, tool: str, exit_code: int, output: str | None = None):
        details = f"Exit code: {exit_code}"
        if output:
            details += f", Output: {output}"
        super().__init__(f"Asset generation failed: {tool}", details)
        self.tool = tool
        self.exit_code = exit_code
        self.output = output


class ExecutableNotFoundError(InstallError):
    """Port executable not found after extraction."""

    def __init__(self, executable: str, search_path: str):
        super().__init__(
            f"Executable not found: {executable}",
            f"Searched in: {search_path}",
        )
        self.executable = executable
        self.search_path = search_path


class ArchiveExtractionError(InstallError):
    """Failed to extract archive."""

    def __init__(self, archive: str, reason: str):
        super().__init__(f"Archive extraction failed: {archive}", reason)
        self.archive = archive
        self.reason = reason


class UnsafeArchiveError(InstallError):
    """Archive contains unsafe paths (path traversal attempt)."""

    def __init__(self, archive: str, unsafe_path: str):
        super().__init__(
            f"Unsafe path in archive: {archive}",
            f"Attempted path traversal: {unsafe_path}",
        )
        self.archive = archive
        self.unsafe_path = unsafe_path


# =============================================================================
# Download Errors
# =============================================================================


class DownloadError(PierError):
    """Base exception for download failures."""

    pass


class NetworkError(DownloadError):
    """Network connectivity issue."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"Network error: {url}", reason)
        self.url = url
        self.reason = reason


class ReleaseNotFoundError(DownloadError):
    """GitHub release not found."""

    def __init__(self, repo: str, version: str | None = None):
        if version:
            super().__init__(f"Release not found: {repo}@{version}")
        else:
            super().__init__(f"No releases found: {repo}")
        self.repo = repo
        self.version = version


class AssetNotFoundError(DownloadError):
    """Release asset matching pattern not found."""

    def __init__(self, repo: str, pattern: str, available: list[str] | None = None):
        details = f"Pattern: {pattern}"
        if available:
            details += f", Available: {', '.join(available[:5])}"
        super().__init__(f"Asset not found in release: {repo}", details)
        self.repo = repo
        self.pattern = pattern
        self.available = available


# =============================================================================
# Configuration Errors
# =============================================================================


class ConfigError(PierError):
    """Base exception for configuration errors."""

    pass


class InvalidConfigError(ConfigError):
    """Configuration file is invalid."""

    def __init__(self, path: str, reason: str):
        super().__init__(f"Invalid configuration: {path}", reason)
        self.path = path
        self.reason = reason


# =============================================================================
# Registry Errors
# =============================================================================


class RegistryError(PierError):
    """Base exception for registry errors."""

    pass


class UnknownPortError(RegistryError):
    """Requested port is not in the registry."""

    def __init__(self, port_id: str):
        super().__init__(f"Unknown port: {port_id}")
        self.port_id = port_id


class UnknownSystemError(RegistryError):
    """Requested system is not in the registry."""

    def __init__(self, system_id: str):
        super().__init__(f"Unknown system: {system_id}")
        self.system_id = system_id


# =============================================================================
# BIOS Errors
# =============================================================================


class BIOSError(PierError):
    """Base exception for BIOS-related errors."""

    pass


class BIOSNotFoundError(BIOSError):
    """BIOS file not found."""

    def __init__(self, filename: str, system: str):
        super().__init__(
            f"BIOS not found: {filename}",
            f"Required for {system}. Run 'pier bios download' to obtain.",
        )
        self.filename = filename
        self.system = system


class BIOSHashMismatchError(BIOSError):
    """BIOS file hash does not match."""

    def __init__(self, filename: str, expected: str, actual: str):
        super().__init__(
            f"BIOS hash mismatch: {filename}",
            f"Expected: {expected}, got: {actual}",
        )
        self.filename = filename
        self.expected = expected
        self.actual = actual


class UnknownBIOSError(BIOSError):
    """BIOS file is not in the registry."""

    def __init__(self, filename: str):
        super().__init__(
            f"Unknown BIOS file: {filename}",
            "This file is not in the BIOS registry.",
        )
        self.filename = filename


# =============================================================================
# Steam Errors
# =============================================================================


class SteamError(PierError):
    """Base exception for Steam-related errors."""

    pass


class SteamNotFoundError(SteamError):
    """Steam installation not found."""

    def __init__(self):
        super().__init__(
            "Steam not found",
            "Ensure Steam is installed at ~/.local/share/Steam or ~/.steam/steam",
        )


class SteamUserNotFoundError(SteamError):
    """No Steam user profiles found."""

    def __init__(self):
        super().__init__(
            "No Steam user found",
            "Log into Steam at least once to create a user profile",
        )


class ShortcutVDFError(SteamError):
    """Error reading/writing shortcuts.vdf."""

    def __init__(self, operation: str, reason: str):
        super().__init__(f"Shortcut VDF {operation} failed", reason)
        self.operation = operation
        self.reason = reason
