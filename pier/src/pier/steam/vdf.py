"""Steam VDF file operations and ID generation."""

import shutil
import zlib
from pathlib import Path
from typing import Any

import vdf

from pier.steam.paths import find_shortcuts_vdf


def generate_app_id(exe: str, name: str) -> int:
    """Generate a deterministic app ID for a non-Steam game.

    Uses CRC32 hash of exe + name, with high bit set to mark as non-Steam.
    This matches the algorithm used by BoilR and Steam ROM Manager.

    Returns a signed 32-bit int for VDF compatibility (which uses signed ints),
    even though Steam interprets these as unsigned.
    """
    combined = exe + name
    crc = zlib.crc32(combined.encode("utf-8")) & 0xFFFFFFFF
    app_id = crc | 0x80000000
    # Convert to signed for VDF library compatibility
    if app_id >= 0x80000000:
        app_id = app_id - 0x100000000
    return app_id


def generate_grid_id(app_id: int) -> int:
    """Generate the ID used for artwork filenames.

    Steam uses the unsigned 32-bit app_id for grid artwork filenames.
    The app_id is stored as signed for VDF, so we convert back to unsigned.
    """
    if app_id < 0:
        return app_id + 0x100000000
    return app_id & 0xFFFFFFFF


def load_shortcuts(path: Path | None = None) -> dict[str, Any]:
    """Load shortcuts from shortcuts.vdf.

    Returns a dict with 'shortcuts' key containing numbered shortcut entries.
    """
    if path is None:
        path = find_shortcuts_vdf()

    if not path or not path.exists():
        return {"shortcuts": {}}

    try:
        data = vdf.binary_loads(path.read_bytes())
        return data
    except Exception:
        return {"shortcuts": {}}


def save_shortcuts(data: dict[str, Any], path: Path | None = None) -> None:
    """Save shortcuts to shortcuts.vdf.

    Creates a backup before writing.
    """
    if path is None:
        path = find_shortcuts_vdf()

    if not path:
        raise RuntimeError("Could not find Steam shortcuts.vdf path")

    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        backup = path.with_suffix(".vdf.bak")
        shutil.copy(path, backup)

    path.write_bytes(vdf.binary_dumps(data))
