"""Shared constants for pier."""

from pathlib import Path

# =============================================================================
# Tags
# =============================================================================

PIER_TAG = "pier"
PORTS_TAG = "Ports"

# =============================================================================
# Timeouts (seconds)
# =============================================================================

HTTP_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 60.0

# =============================================================================
# Chunk sizes (bytes)
# =============================================================================

DOWNLOAD_CHUNK_SIZE = 65536
HASH_CHUNK_SIZE = 65536

# =============================================================================
# Display limits
# =============================================================================

ROM_LIST_DISPLAY_LIMIT = 200
SEARCH_RESULT_LIMIT = 50
RELEASE_FETCH_LIMIT = 10

# =============================================================================
# Paths
# =============================================================================

DEFAULT_EMULATION_DIR = Path.home() / "Emulation"

STEAM_USERDATA_PATHS = [
    Path.home() / ".local/share/Steam/userdata",
    Path.home() / ".steam/steam/userdata",
]

# =============================================================================
# File extensions
# =============================================================================

ARCHIVE_EXTENSIONS = frozenset({".zip", ".tar", ".gz", ".tgz", ".xz"})
APPIMAGE_EXTENSION = ".appimage"

ROM_EXTENSIONS = frozenset({
    ".z64", ".n64", ".v64",  # N64
    ".sfc", ".smc",          # SNES
    ".nes",                  # NES
    ".gba",                  # GBA
    ".md", ".bin", ".gen",   # Genesis
    ".iso", ".cue",          # CD-based
    ".rvz", ".gcm", ".wbfs", # GameCube/Wii
})

# =============================================================================
# URLs
# =============================================================================

MYRIENT_BASE_URL = "https://myrient.erista.me/files"
GITHUB_API_BASE = "https://api.github.com"
STEAMGRIDDB_API_BASE = "https://www.steamgriddb.com/api/v2"
LIBRETRO_THUMBNAILS_BASE = "https://thumbnails.libretro.com"
RETROARCH_SYSTEM_RAW = "https://raw.githubusercontent.com/Abdess/retroarch_system/libretro"

# =============================================================================
# User agent
# =============================================================================

USER_AGENT = "pier-htpc"

# =============================================================================
# Executables
# =============================================================================

# NixOS steam-run wrapper for running Steam games
STEAM_RUN_EXECUTABLE = "/run/current-system/sw/bin/steam-run"
