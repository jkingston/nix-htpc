"""No-Intro/Redump DAT file parsing for ROM verification.

DAT files are XML files containing metadata about known good ROM dumps,
including SHA1, MD5, and CRC32 hashes for verification.

DAT files can be downloaded from:
- No-Intro: https://datomatic.no-intro.org/
- Redump: http://redump.org/
"""

from dataclasses import dataclass
from pathlib import Path

try:
    import xmltodict

    HAS_XMLTODICT = True
except ImportError:
    HAS_XMLTODICT = False


@dataclass
class RomInfo:
    """Information about a ROM from a DAT file."""

    name: str  # Game name
    sha1: str  # SHA1 hash (lowercase)
    md5: str  # MD5 hash (lowercase)
    crc32: str  # CRC32 hash (uppercase)
    size: int  # File size in bytes


class DATFileError(Exception):
    """Error parsing or using DAT files."""

    pass


def parse_dat_file(path: Path) -> dict[str, RomInfo]:
    """Parse a No-Intro/Redump DAT file.

    Args:
        path: Path to the DAT file (XML format).

    Returns:
        Dictionary mapping SHA1 hashes to RomInfo objects.

    Raises:
        DATFileError: If parsing fails or xmltodict is not installed.
    """
    if not HAS_XMLTODICT:
        msg = "xmltodict is required for DAT file parsing. Install with: pip install xmltodict"
        raise DATFileError(msg)

    try:
        with open(path, "rb") as f:
            data = xmltodict.parse(f)
    except Exception as e:
        raise DATFileError(f"Failed to parse DAT file: {e}") from e

    # DAT files have a <datafile> root with <game> elements
    datafile = data.get("datafile", {})
    games = datafile.get("game", [])

    # Ensure games is a list
    if isinstance(games, dict):
        games = [games]

    result: dict[str, RomInfo] = {}

    for game in games:
        name = game.get("@name", "")

        # Get ROM info - can be single dict or list
        roms = game.get("rom", [])
        if isinstance(roms, dict):
            roms = [roms]

        for rom in roms:
            sha1 = rom.get("@sha1", "").lower()
            md5 = rom.get("@md5", "").lower()
            crc32 = rom.get("@crc", "").upper()
            size = int(rom.get("@size", 0))

            if sha1:
                result[sha1] = RomInfo(
                    name=name,
                    sha1=sha1,
                    md5=md5,
                    crc32=crc32,
                    size=size,
                )

    return result


def lookup_by_sha1(dat_data: dict[str, RomInfo], sha1: str) -> RomInfo | None:
    """Look up ROM info by SHA1 hash.

    Args:
        dat_data: Parsed DAT data from parse_dat_file().
        sha1: SHA1 hash to look up (case-insensitive).

    Returns:
        RomInfo if found, None otherwise.
    """
    return dat_data.get(sha1.lower())


def lookup_by_name(dat_data: dict[str, RomInfo], name: str) -> list[RomInfo]:
    """Look up ROM info by name (partial match).

    Args:
        dat_data: Parsed DAT data from parse_dat_file().
        name: Name to search for (case-insensitive substring).

    Returns:
        List of matching RomInfo objects.
    """
    name_lower = name.lower()
    return [info for info in dat_data.values() if name_lower in info.name.lower()]
