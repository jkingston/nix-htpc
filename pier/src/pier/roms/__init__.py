"""ROM management module."""

from pier.roms.hashing import (
    N64Format,
    compute_crc32,
    compute_md5,
    compute_sha1,
    convert_to_z64,
    detect_n64_format,
    verify_hash,
)
from pier.roms.myrient import MyrientClient, MyrientError, MyrientFile
from pier.roms.scanner import Game, scan_roms
from pier.roms.systems import SYSTEMS, System

__all__ = [
    # Systems
    "SYSTEMS",
    "System",
    # Scanner
    "Game",
    "scan_roms",
    # Hashing
    "N64Format",
    "compute_crc32",
    "compute_md5",
    "compute_sha1",
    "convert_to_z64",
    "detect_n64_format",
    "verify_hash",
    # Myrient
    "MyrientClient",
    "MyrientError",
    "MyrientFile",
]
