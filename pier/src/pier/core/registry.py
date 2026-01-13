"""Port and system registry for pier."""

from dataclasses import dataclass, field
from enum import Enum


class AssetGenerator(Enum):
    """Methods for generating game assets from ROMs."""

    TORCH = "torch"  # HarbourMasters torch tool
    OPENGOAL = "opengoal"  # OpenGOAL extractor
    COPY = "copy"  # Just copy ROM to expected location
    NONE = "none"  # No asset generation needed


@dataclass
class Mod:
    """A mod or texture pack for a port."""

    id: str
    name: str
    repo: str  # GitHub repo
    asset_pattern: str  # Pattern to match release asset
    description: str


@dataclass
class RomRequirement:
    """ROM required by a port."""

    name: str  # Display name
    filename: str  # Expected filename (e.g., "Mario Kart 64 (USA).z64")
    system: str  # System ID (e.g., "n64")
    myrient_path: str  # Path on myrient (URL-encoded)
    hash_type: str  # "sha1" or "md5"
    hash_value: str  # Expected hash
    copy_as: str | None = None  # Rename when copying for port


@dataclass
class Port:
    """A native game port."""

    id: str
    name: str
    game: str  # Original game name
    repo: str  # GitHub repo
    asset_pattern: str  # Pattern to match Linux release asset
    executable: str  # Executable name after extraction
    rom: RomRequirement
    asset_generator: AssetGenerator
    mods: list[Mod] = field(default_factory=list)
    extract_torch: bool = False  # Whether to extract torch from AppImage


@dataclass
class System:
    """A ROM system/console."""

    id: str
    name: str
    myrient_path: str  # Base path on myrient
    extensions: list[str]  # Valid file extensions
    libretro_name: str  # Name for libretro-thumbnails
    emulator_wrapper: str = ""  # Wrapper script (e.g., "dolphin-wrapper")
    emulator_args: str = ""  # Args for wrapper (e.g., RetroArch core name)


# System definitions
SYSTEMS: dict[str, System] = {
    "n64": System(
        id="n64",
        name="Nintendo 64",
        myrient_path="No-Intro/Nintendo%20-%20Nintendo%2064%20(BigEndian)",
        extensions=[".z64", ".n64", ".v64"],
        libretro_name="Nintendo - Nintendo 64",
        emulator_wrapper="retroarch-wrapper",
        emulator_args="mupen64plus_next",
    ),
    "snes": System(
        id="snes",
        name="Super Nintendo",
        myrient_path="No-Intro/Nintendo%20-%20Super%20Nintendo%20Entertainment%20System",
        extensions=[".sfc", ".smc"],
        libretro_name="Nintendo - Super Nintendo Entertainment System",
        emulator_wrapper="retroarch-wrapper",
        emulator_args="snes9x",
    ),
    "nes": System(
        id="nes",
        name="Nintendo Entertainment System",
        myrient_path="No-Intro/Nintendo%20-%20Nintendo%20Entertainment%20System%20(Headered)",
        extensions=[".nes"],
        libretro_name="Nintendo - Nintendo Entertainment System",
        emulator_wrapper="retroarch-wrapper",
        emulator_args="mesen",
    ),
    "gba": System(
        id="gba",
        name="Game Boy Advance",
        myrient_path="No-Intro/Nintendo%20-%20Game%20Boy%20Advance",
        extensions=[".gba"],
        libretro_name="Nintendo - Game Boy Advance",
        emulator_wrapper="retroarch-wrapper",
        emulator_args="mgba",
    ),
    "genesis": System(
        id="genesis",
        name="Sega Genesis / Mega Drive",
        myrient_path="No-Intro/Sega%20-%20Mega%20Drive%20-%20Genesis",
        extensions=[".md", ".bin", ".gen"],
        libretro_name="Sega - Mega Drive - Genesis",
        emulator_wrapper="retroarch-wrapper",
        emulator_args="genesis_plus_gx",
    ),
    "ps1": System(
        id="ps1",
        name="PlayStation",
        myrient_path="Redump/Sony%20-%20PlayStation",
        extensions=[".bin", ".cue", ".iso"],
        libretro_name="Sony - PlayStation",
        emulator_wrapper="duckstation-wrapper",
    ),
    "ps2": System(
        id="ps2",
        name="PlayStation 2",
        myrient_path="Redump/Sony%20-%20PlayStation%202",
        extensions=[".iso", ".bin", ".cue"],
        libretro_name="Sony - PlayStation 2",
        emulator_wrapper="pcsx2-wrapper",
    ),
    "gc": System(
        id="gc",
        name="GameCube",
        myrient_path="Redump/Nintendo%20-%20GameCube%20-%20NKit%20RVZ",
        extensions=[".rvz", ".iso", ".gcm"],
        libretro_name="Nintendo - GameCube",
        emulator_wrapper="dolphin-wrapper",
    ),
    "wii": System(
        id="wii",
        name="Wii",
        myrient_path="Redump/Nintendo%20-%20Wii%20-%20NKit%20RVZ",
        extensions=[".rvz", ".iso", ".wbfs"],
        libretro_name="Nintendo - Wii",
        emulator_wrapper="dolphin-wrapper",
    ),
    "psp": System(
        id="psp",
        name="PlayStation Portable",
        myrient_path="Redump/Sony%20-%20PlayStation%20Portable",
        extensions=[".iso", ".cso"],
        libretro_name="Sony - PlayStation Portable",
        emulator_wrapper="ppsspp-wrapper",
    ),
    "nds": System(
        id="nds",
        name="Nintendo DS",
        myrient_path="No-Intro/Nintendo%20-%20Nintendo%20DS%20(Decrypted)",
        extensions=[".nds"],
        libretro_name="Nintendo - Nintendo DS",
        emulator_wrapper="melonds-wrapper",
    ),
    "dreamcast": System(
        id="dreamcast",
        name="Sega Dreamcast",
        myrient_path="Redump/Sega%20-%20Dreamcast",
        extensions=[".gdi", ".cdi", ".chd"],
        libretro_name="Sega - Dreamcast",
        emulator_wrapper="flycast-wrapper",
    ),
}


# Port definitions
PORTS: dict[str, Port] = {
    "spaghettikart": Port(
        id="spaghettikart",
        name="SpaghettiKart",
        game="Mario Kart 64",
        repo="HarbourMasters/SpaghettiKart",
        asset_pattern="Linux",
        executable="spaghetti.AppImage",
        asset_generator=AssetGenerator.TORCH,
        extract_torch=True,
        rom=RomRequirement(
            name="Mario Kart 64 (USA)",
            filename="Mario Kart 64 (USA).z64",
            system="n64",
            myrient_path="Mario%20Kart%2064%20(USA).zip",
            hash_type="sha1",
            hash_value="579C48E211AE952530FFC8738709F078D5DD215E",
        ),
        mods=[
            Mod(
                id="hd-textures",
                name="MK64 Reloaded HD",
                repo="GhostlyDark/MK64-Reloaded-SK",
                asset_pattern=".o2r",
                description="UHD texture pack",
            ),
        ],
    ),
    "soh": Port(
        id="soh",
        name="Ship of Harkinian",
        game="Ocarina of Time",
        repo="HarbourMasters/Shipwright",
        asset_pattern="Linux",
        executable="soh.AppImage",
        asset_generator=AssetGenerator.TORCH,
        extract_torch=True,
        rom=RomRequirement(
            name="Ocarina of Time (USA)",
            filename="Legend of Zelda, The - Ocarina of Time (USA).z64",
            system="n64",
            myrient_path="Legend%20of%20Zelda%2C%20The%20-%20Ocarina%20of%20Time%20(USA).zip",
            hash_type="sha1",
            hash_value="AD69C91157F6705E8AB06C79FE08AAD47BB57BA7",  # US 1.0
        ),
        mods=[
            Mod(
                id="hd-textures",
                name="OoT Reloaded HD",
                repo="GhostlyDark/OoT-Reloaded-SoH",
                asset_pattern=".o2r",
                description="UHD texture pack (Nerrel HD base)",
            ),
        ],
    ),
    "2ship": Port(
        id="2ship",
        name="2Ship2Harkinian",
        game="Majora's Mask",
        repo="HarbourMasters/2ship2harkinian",
        asset_pattern="Linux",
        executable="2s2h.AppImage",
        asset_generator=AssetGenerator.TORCH,
        extract_torch=True,
        rom=RomRequirement(
            name="Majora's Mask (USA)",
            filename="Legend of Zelda, The - Majora's Mask (USA).z64",
            system="n64",
            myrient_path="Legend%20of%20Zelda%2C%20The%20-%20Majora%27s%20Mask%20(USA).zip",
            hash_type="sha1",
            hash_value="D6133ACE5AFAA0882CF214CF88DABA39E266C078",
        ),
        mods=[
            Mod(
                id="hd-textures",
                name="MM Reloaded HD",
                repo="GhostlyDark/MM-Reloaded-2S2H",
                asset_pattern=".o2r",
                description="UHD texture pack",
            ),
        ],
    ),
    "starship": Port(
        id="starship",
        name="Starship",
        game="Star Fox 64",
        repo="HarbourMasters/Starship",
        asset_pattern="Linux",
        executable="starship.AppImage",
        asset_generator=AssetGenerator.TORCH,
        extract_torch=True,
        rom=RomRequirement(
            name="Star Fox 64 (USA)",
            filename="Star Fox 64 (USA).z64",
            system="n64",
            myrient_path="Star%20Fox%2064%20(USA).zip",
            hash_type="sha1",
            hash_value="D8B1088520F7C5F81433292A9258C1184AFA1457",
        ),
        mods=[],
    ),
    "sm64coopdx": Port(
        id="sm64coopdx",
        name="SM64 Coop DX",
        game="Super Mario 64 (Multiplayer)",
        repo="coop-deluxe/sm64coopdx",
        asset_pattern="Linux",
        executable="sm64coopdx.AppImage",
        asset_generator=AssetGenerator.COPY,
        rom=RomRequirement(
            name="Super Mario 64 (USA)",
            filename="Super Mario 64 (USA).z64",
            system="n64",
            myrient_path="Super%20Mario%2064%20(USA).zip",
            hash_type="sha1",
            hash_value="9BEF1128717F958171A4AFAC3ED78EE2BB4E86CE",
            copy_as="baserom.us.z64",
        ),
        mods=[],
    ),
    "perfect-dark": Port(
        id="perfect-dark",
        name="Perfect Dark",
        game="Perfect Dark",
        repo="fgsfdsfgs/perfect_dark",
        asset_pattern="linux",
        executable="pd",
        asset_generator=AssetGenerator.COPY,
        rom=RomRequirement(
            name="Perfect Dark (USA) (Rev 1)",
            filename="Perfect Dark (USA) (Rev 1).z64",
            system="n64",
            myrient_path="Perfect%20Dark%20(USA)%20(Rev%201).zip",
            hash_type="md5",
            hash_value="e03b088b6ac9e0080440efed07c1e40f",
            copy_as="data/pd.ntsc-final.z64",
        ),
        mods=[],
    ),
    "opengoal-jak1": Port(
        id="opengoal-jak1",
        name="OpenGOAL - Jak 1",
        game="Jak and Daxter: The Precursor Legacy",
        repo="open-goal/jak-project",
        asset_pattern="linux",
        executable="gk",
        asset_generator=AssetGenerator.OPENGOAL,
        rom=RomRequirement(
            name="Jak and Daxter (USA)",
            filename="Jak and Daxter - The Precursor Legacy (USA).iso",
            system="ps2",
            myrient_path="Jak%20and%20Daxter%20-%20The%20Precursor%20Legacy%20(USA).zip",
            hash_type="sha1",
            hash_value="",  # Multiple valid dumps
        ),
        mods=[],
    ),
    "opengoal-jak2": Port(
        id="opengoal-jak2",
        name="OpenGOAL - Jak II",
        game="Jak II",
        repo="open-goal/jak-project",
        asset_pattern="linux",
        executable="gk",
        asset_generator=AssetGenerator.OPENGOAL,
        rom=RomRequirement(
            name="Jak II (USA)",
            filename="Jak II (USA).iso",
            system="ps2",
            myrient_path="Jak%20II%20(USA).zip",
            hash_type="sha1",
            hash_value="",  # Multiple valid dumps
        ),
        mods=[],
    ),
    "opengoal-jak3": Port(
        id="opengoal-jak3",
        name="OpenGOAL - Jak 3",
        game="Jak 3",
        repo="open-goal/jak-project",
        asset_pattern="linux",
        executable="gk",
        asset_generator=AssetGenerator.OPENGOAL,
        rom=RomRequirement(
            name="Jak 3 (USA)",
            filename="Jak 3 (USA).iso",
            system="ps2",
            myrient_path="Jak%203%20(USA).zip",
            hash_type="sha1",
            hash_value="",  # Multiple valid dumps
        ),
        mods=[],
    ),
}


def get_port(port_id: str) -> Port | None:
    """Get a port by ID."""
    return PORTS.get(port_id)


def get_system(system_id: str) -> System | None:
    """Get a system by ID."""
    return SYSTEMS.get(system_id)


def list_ports() -> list[Port]:
    """List all available ports."""
    return list(PORTS.values())


def list_systems() -> list[System]:
    """List all available systems."""
    return list(SYSTEMS.values())
