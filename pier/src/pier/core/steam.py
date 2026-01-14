"""Steam shortcut management using VDF format."""

import binascii
import struct
from dataclasses import dataclass, field
from pathlib import Path

import vdf

from pier.core.constants import PIER_TAG as _PIER_TAG
from pier.core.constants import STEAM_USERDATA_PATHS
from pier.core.errors import ShortcutVDFError, SteamNotFoundError, SteamUserNotFoundError


@dataclass
class Shortcut:
    """A non-Steam game shortcut."""

    appid: int
    app_name: str
    exe: str
    start_dir: str
    launch_options: str = ""
    icon: str = ""
    tags: list[str] = field(default_factory=list)
    is_hidden: bool = False
    allow_desktop_config: bool = True
    allow_overlay: bool = True

    @property
    def grid_id(self) -> int:
        """Get the unsigned ID used for grid image filenames."""
        return generate_grid_id(self.exe, self.app_name)


def generate_appid(exe: str, app_name: str) -> int:
    """Generate Steam shortcut appid from exe path and name.

    This matches Steam's internal algorithm for non-Steam game IDs.
    The exe should include quotes (as stored in shortcuts.vdf).
    Returns an unsigned 32-bit integer as Steam expects.
    """
    key = exe + app_name
    crc = binascii.crc32(key.encode("utf-8")) & 0xFFFFFFFF
    # Set high bit to mark as non-Steam game, keep as unsigned
    return (crc | 0x80000000) & 0xFFFFFFFF


def generate_grid_id(exe: str, app_name: str) -> int:
    """Generate the unsigned appid used for grid image filenames.

    This is the same CRC32 calculation but kept unsigned for file naming.
    """
    key = exe + app_name
    crc = binascii.crc32(key.encode("utf-8")) & 0xFFFFFFFF
    return crc | 0x80000000


def find_steam_userdata() -> Path:
    """Find the Steam userdata directory for the current user.

    Returns:
        Path to the first user's data directory

    Raises:
        SteamNotFoundError: If Steam installation not found
        SteamUserNotFoundError: If no Steam user profiles found
    """
    steam_dir = None
    for path in STEAM_USERDATA_PATHS:
        if path.exists():
            steam_dir = path
            break

    if steam_dir is None:
        raise SteamNotFoundError()

    user_ids = [d for d in steam_dir.iterdir() if d.is_dir() and d.name.isdigit()]
    if not user_ids:
        raise SteamUserNotFoundError()

    return user_ids[0]


def get_shortcuts_path() -> Path:
    """Get the path to shortcuts.vdf."""
    return find_steam_userdata() / "config" / "shortcuts.vdf"


def get_grid_path() -> Path:
    """Get the path to the grid artwork directory."""
    return find_steam_userdata() / "config" / "grid"


class VDFReader:
    """Reader for Steam's binary VDF format."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def read_byte(self) -> bytes:
        """Read a single byte."""
        if self.pos >= len(self.data):
            return b""
        b = bytes([self.data[self.pos]])
        self.pos += 1
        return b

    def read_string(self) -> str:
        """Read a null-terminated string."""
        end = self.data.find(b"\x00", self.pos)
        if end == -1:
            end = len(self.data)
        s = self.data[self.pos : end].decode("utf-8", errors="replace")
        self.pos = end + 1
        return s

    def read_int32(self) -> int:
        """Read a 32-bit unsigned integer."""
        val = struct.unpack("<I", self.data[self.pos : self.pos + 4])[0]
        self.pos += 4
        return val

    def read_dict(self) -> dict:
        """Read a VDF dictionary."""
        result = {}
        while True:
            type_byte = self.read_byte()
            if type_byte == VDF_TYPE_END or type_byte == b"":
                break

            key = self.read_string()

            if type_byte == VDF_TYPE_NONE:
                result[key] = self.read_dict()
            elif type_byte == VDF_TYPE_STRING:
                result[key] = self.read_string()
            elif type_byte == VDF_TYPE_INT32:
                result[key] = self.read_int32()
            else:
                # Skip unknown types
                pass

        return result


class VDFWriter:
    """Writer for Steam's binary VDF format."""

    def __init__(self):
        self.data = bytearray()

    def write_byte(self, b: bytes):
        """Write a single byte."""
        self.data.extend(b)

    def write_string(self, s: str):
        """Write a null-terminated string."""
        self.data.extend(s.encode("utf-8"))
        self.data.append(0)

    def write_int32(self, val: int):
        """Write a 32-bit unsigned integer."""
        self.data.extend(struct.pack("<I", val & 0xFFFFFFFF))

    def write_dict(self, d: dict, name: str | None = None):
        """Write a VDF dictionary."""
        if name is not None:
            self.write_byte(VDF_TYPE_NONE)
            self.write_string(name)

        for key, value in d.items():
            if isinstance(value, dict):
                self.write_dict(value, key)
            elif isinstance(value, int):
                self.write_byte(VDF_TYPE_INT32)
                self.write_string(key)
                self.write_int32(value)
            else:
                self.write_byte(VDF_TYPE_STRING)
                self.write_string(key)
                self.write_string(str(value))

        self.write_byte(VDF_TYPE_END)

    def get_data(self) -> bytes:
        """Get the written data."""
        return bytes(self.data)


def load_shortcuts(path: Path | None = None) -> dict[str, dict]:
    """Load shortcuts from VDF file.

    Args:
        path: Path to shortcuts.vdf, or None to use default

    Returns:
        Dictionary of shortcuts indexed by string numbers

    Raises:
        ShortcutVDFError: If the file exists but cannot be parsed
    """
    if path is None:
        path = get_shortcuts_path()

    if not path.exists():
        return {}  # No file is OK - means no shortcuts yet

    try:
        data = path.read_bytes()
    except OSError as e:
        raise ShortcutVDFError("read", f"Cannot read {path}: {e}") from e

    if not data:
        return {}  # Empty file is OK

    try:
        result = vdf.binary_loads(data)
        return result.get("shortcuts", {})
    except Exception as e:
        raise ShortcutVDFError("parse", f"Cannot parse {path}: {e}") from e


def save_shortcuts(shortcuts: dict[str, dict], path: Path | None = None):
    """Save shortcuts to VDF file.

    Args:
        shortcuts: Dictionary of shortcuts indexed by string numbers
        path: Path to save to, or None to use default
    """
    if path is None:
        path = get_shortcuts_path()

    path.parent.mkdir(parents=True, exist_ok=True)

    # Use the vdf library for correct binary format
    data = vdf.binary_dumps({"shortcuts": shortcuts})
    path.write_bytes(data)


def _to_signed_int32(val: int) -> int:
    """Convert unsigned int32 to signed for vdf library compatibility."""
    return struct.unpack('<i', struct.pack('<I', val & 0xFFFFFFFF))[0]


def shortcut_to_dict(shortcut: Shortcut) -> dict:
    """Convert a Shortcut to a VDF dictionary entry."""
    return {
        "appid": _to_signed_int32(shortcut.appid),
        "AppName": shortcut.app_name,
        "Exe": shortcut.exe,
        "StartDir": shortcut.start_dir,
        "icon": shortcut.icon,
        "ShortcutPath": "",
        "LaunchOptions": shortcut.launch_options,
        "IsHidden": 1 if shortcut.is_hidden else 0,
        "AllowDesktopConfig": 1 if shortcut.allow_desktop_config else 0,
        "AllowOverlay": 1 if shortcut.allow_overlay else 0,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "tags": {str(i): tag for i, tag in enumerate(shortcut.tags)},
    }


def dict_to_shortcut(data: dict) -> Shortcut:
    """Convert a VDF dictionary entry to a Shortcut."""
    tags = []
    if "tags" in data and isinstance(data["tags"], dict):
        tags = [data["tags"][k] for k in sorted(data["tags"].keys(), key=int)]

    return Shortcut(
        appid=data.get("appid", 0),
        app_name=data.get("AppName", ""),
        exe=data.get("Exe", ""),
        start_dir=data.get("StartDir", ""),
        launch_options=data.get("LaunchOptions", ""),
        icon=data.get("icon", ""),
        tags=tags,
        is_hidden=bool(data.get("IsHidden", 0)),
        allow_desktop_config=bool(data.get("AllowDesktopConfig", 1)),
        allow_overlay=bool(data.get("AllowOverlay", 1)),
    )


class SteamLibrary:
    """Manages Steam shortcuts for non-Steam games."""

    PIER_TAG = _PIER_TAG  # Tag used to identify pier-managed shortcuts

    def __init__(self, userdata_path: Path | None = None):
        """Initialize the library.

        Args:
            userdata_path: Path to Steam userdata directory, or None to auto-detect
        """
        if userdata_path is None:
            userdata_path = find_steam_userdata()
        self.userdata_path = userdata_path
        self.shortcuts_path = userdata_path / "config" / "shortcuts.vdf"
        self.grid_path = userdata_path / "config" / "grid"

    def load(self) -> list[Shortcut]:
        """Load all shortcuts."""
        shortcuts_dict = load_shortcuts(self.shortcuts_path)
        return [dict_to_shortcut(v) for v in shortcuts_dict.values()]

    def save(self, shortcuts: list[Shortcut]):
        """Save all shortcuts."""
        shortcuts_dict = {str(i): shortcut_to_dict(s) for i, s in enumerate(shortcuts)}
        save_shortcuts(shortcuts_dict, self.shortcuts_path)

    def find_shortcut(self, app_name: str) -> Shortcut | None:
        """Find a shortcut by app name."""
        for shortcut in self.load():
            if shortcut.app_name == app_name:
                return shortcut
        return None

    def add_shortcut(
        self,
        app_name: str,
        exe: str,
        start_dir: str = "",
        launch_options: str = "",
        icon: str = "",
        tags: list[str] | None = None,
    ) -> Shortcut:
        """Add or update a shortcut.

        Args:
            app_name: Display name in Steam
            exe: Executable path (will be quoted)
            start_dir: Working directory (will be quoted)
            launch_options: Additional launch arguments
            icon: Path to icon file
            tags: Steam tags/categories

        Returns:
            The created/updated shortcut
        """
        if tags is None:
            tags = []

        # Add pier tag for tracking
        if self.PIER_TAG not in tags:
            tags = [self.PIER_TAG] + tags

        # Quote exe and start_dir as Steam expects
        quoted_exe = f'"{exe}"' if not exe.startswith('"') else exe
        quoted_dir = f'"{start_dir}"' if start_dir and not start_dir.startswith('"') else start_dir

        # Generate appid
        appid = generate_appid(quoted_exe, app_name)

        shortcut = Shortcut(
            appid=appid,
            app_name=app_name,
            exe=quoted_exe,
            start_dir=quoted_dir,
            launch_options=launch_options,
            icon=icon,
            tags=tags,
        )

        # Load existing shortcuts
        shortcuts = self.load()

        # Remove any existing shortcut with same name
        shortcuts = [s for s in shortcuts if s.app_name != app_name]

        # Add new shortcut
        shortcuts.append(shortcut)

        # Save
        self.save(shortcuts)

        return shortcut

    def remove_shortcut(self, app_name: str) -> bool:
        """Remove a shortcut by name.

        Returns:
            True if shortcut was found and removed
        """
        shortcuts = self.load()
        original_count = len(shortcuts)
        shortcuts = [s for s in shortcuts if s.app_name != app_name]

        if len(shortcuts) < original_count:
            self.save(shortcuts)
            return True
        return False

    def list_pier_shortcuts(self) -> list[Shortcut]:
        """List all shortcuts with the pier tag."""
        return [s for s in self.load() if self.PIER_TAG in s.tags]

    def install_artwork(
        self,
        shortcut: Shortcut,
        grid: Path | None = None,
        hero: Path | None = None,
        logo: Path | None = None,
        icon: Path | None = None,
    ):
        """Install artwork files for a shortcut.

        Args:
            shortcut: The shortcut to install artwork for
            grid: Path to vertical grid image (600x900)
            hero: Path to hero image (1920x620)
            logo: Path to logo image (transparent PNG)
            icon: Path to icon image
        """
        self.grid_path.mkdir(parents=True, exist_ok=True)
        grid_id = shortcut.grid_id

        if grid:
            dest = self.grid_path / f"{grid_id}p{grid.suffix}"
            dest.write_bytes(grid.read_bytes())

        if hero:
            dest = self.grid_path / f"{grid_id}_hero{hero.suffix}"
            dest.write_bytes(hero.read_bytes())

        if logo:
            dest = self.grid_path / f"{grid_id}_logo{logo.suffix}"
            dest.write_bytes(logo.read_bytes())

        if icon:
            dest = self.grid_path / f"{grid_id}_icon{icon.suffix}"
            dest.write_bytes(icon.read_bytes())

    def install_artwork_from_cache(self, shortcut: Shortcut, cached: "CachedArtwork") -> None:
        """Install artwork from cache to Steam grid directory.

        Args:
            shortcut: The shortcut to install artwork for
            cached: CachedArtwork with paths to cached image files
        """
        from pier.core.artwork_cache import CachedArtwork  # noqa: F811

        self.install_artwork(
            shortcut,
            grid=cached.grid,
            hero=cached.hero,
            logo=cached.logo,
            icon=cached.icon,
        )
