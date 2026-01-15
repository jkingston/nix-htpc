"""ROM system definitions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class System:
    """A ROM system/console."""

    id: str
    name: str
    extensions: frozenset[str]
    myrient_path: str
    emulator: str  # Command to launch ROMs


# Extensions to ignore when scanning ROM directories
IGNORED_EXTENSIONS = frozenset({
    ".txt", ".nfo", ".jpg", ".jpeg", ".png", ".gif", ".xml", ".json",
    ".sav", ".srm", ".log", ".cue", ".m3u", ".html", ".htm", ".pdf",
    ".state", ".oops", ".auto",
})


SYSTEMS: dict[str, System] = {
    "n64": System(
        id="n64",
        name="Nintendo 64",
        extensions=frozenset({".z64", ".n64", ".v64"}),
        myrient_path="No-Intro/Nintendo%20-%20Nintendo%2064%20(BigEndian)",
        emulator="retroarch-wrapper mupen64plus_next",
    ),
    "snes": System(
        id="snes",
        name="Super Nintendo",
        extensions=frozenset({".sfc", ".smc"}),
        myrient_path="No-Intro/Nintendo%20-%20Super%20Nintendo%20Entertainment%20System",
        emulator="retroarch-wrapper snes9x",
    ),
    "nes": System(
        id="nes",
        name="Nintendo Entertainment System",
        extensions=frozenset({".nes"}),
        myrient_path="No-Intro/Nintendo%20-%20Nintendo%20Entertainment%20System%20(Headered)",
        emulator="retroarch-wrapper mesen",
    ),
    "gba": System(
        id="gba",
        name="Game Boy Advance",
        extensions=frozenset({".gba"}),
        myrient_path="No-Intro/Nintendo%20-%20Game%20Boy%20Advance",
        emulator="retroarch-wrapper mgba",
    ),
    "gb": System(
        id="gb",
        name="Game Boy",
        extensions=frozenset({".gb"}),
        myrient_path="No-Intro/Nintendo%20-%20Game%20Boy",
        emulator="retroarch-wrapper gambatte",
    ),
    "gbc": System(
        id="gbc",
        name="Game Boy Color",
        extensions=frozenset({".gbc"}),
        myrient_path="No-Intro/Nintendo%20-%20Game%20Boy%20Color",
        emulator="retroarch-wrapper gambatte",
    ),
    "nds": System(
        id="nds",
        name="Nintendo DS",
        extensions=frozenset({".nds"}),
        myrient_path="No-Intro/Nintendo%20-%20Nintendo%20DS%20(Decrypted)",
        emulator="melonds-wrapper",
    ),
    "genesis": System(
        id="genesis",
        name="Sega Genesis / Mega Drive",
        extensions=frozenset({".md", ".bin", ".gen"}),
        myrient_path="No-Intro/Sega%20-%20Mega%20Drive%20-%20Genesis",
        emulator="retroarch-wrapper genesis_plus_gx",
    ),
    "ps1": System(
        id="ps1",
        name="PlayStation",
        extensions=frozenset({".bin", ".cue", ".iso", ".chd"}),
        myrient_path="Redump/Sony%20-%20PlayStation",
        emulator="duckstation-wrapper",
    ),
    "ps2": System(
        id="ps2",
        name="PlayStation 2",
        extensions=frozenset({".iso", ".bin", ".chd"}),
        myrient_path="Redump/Sony%20-%20PlayStation%202",
        emulator="pcsx2-wrapper",
    ),
    "psp": System(
        id="psp",
        name="PlayStation Portable",
        extensions=frozenset({".iso", ".cso"}),
        myrient_path="Redump/Sony%20-%20PlayStation%20Portable",
        emulator="ppsspp-wrapper",
    ),
    "gc": System(
        id="gc",
        name="GameCube",
        extensions=frozenset({".rvz", ".iso", ".gcm"}),
        myrient_path="Redump/Nintendo%20-%20GameCube%20-%20NKit%20RVZ",
        emulator="dolphin-wrapper",
    ),
    "wii": System(
        id="wii",
        name="Wii",
        extensions=frozenset({".rvz", ".iso", ".wbfs"}),
        myrient_path="Redump/Nintendo%20-%20Wii%20-%20NKit%20RVZ",
        emulator="dolphin-wrapper",
    ),
    "dreamcast": System(
        id="dreamcast",
        name="Sega Dreamcast",
        extensions=frozenset({".gdi", ".cdi", ".chd"}),
        myrient_path="Redump/Sega%20-%20Dreamcast",
        emulator="flycast-wrapper",
    ),
}


def get_system(system_id: str) -> System | None:
    """Get a system by ID."""
    return SYSTEMS.get(system_id)


def get_system_for_extension(ext: str) -> list[System]:
    """Get all systems that support a given extension."""
    ext_lower = ext.lower()
    return [s for s in SYSTEMS.values() if ext_lower in s.extensions]
