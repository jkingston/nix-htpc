"""PC port definitions registry.

This module defines the Port dataclass and the registry of supported PC ports.
Each port specifies its source repository, required ROM hashes, and installation details.
"""

from dataclasses import dataclass
from enum import Enum


class PortType(Enum):
    """How a port is distributed and installed."""

    HARBOUR_MASTERS = "harbour_masters"  # OTR/O2R-based (SoH, 2Ship, Starship, SpaghettiKart)
    OPENGOAL = "opengoal"  # ISO extraction (Jak series)
    DIRECT_PORT = "direct_port"  # Direct ROM conversion (Perfect Dark)


@dataclass(frozen=True)
class Enhancement:
    """An enhancement pack for a port (HD textures, etc.)."""

    id: str  # Unique identifier (e.g., "oot-reloaded")
    name: str  # Display name (e.g., "OoT Reloaded HD Textures")
    repo: str  # GitHub repo (e.g., "GhostlyDark/OoT-Reloaded-SoH")
    asset_pattern: str  # Glob pattern for release asset ("*.o2r", "*.zip")
    install_subdir: str = "mods"  # Relative to port install dir


@dataclass(frozen=True)
class Port:
    """A PC port definition."""

    id: str  # Unique identifier (e.g., "soh", "spaghettikart")
    name: str  # Display name (e.g., "Ship of Harkinian")
    type: PortType  # Installation type
    github_repo: str  # Source repository (e.g., "HarbourMasters/Shipwright")
    system: str  # ROM system ID (e.g., "n64", "ps2")
    rom_search_name: str  # Name for searching Myrient (e.g., "Mario Kart 64 (USA)")
    required_hashes: frozenset[str]  # SHA1 hashes of compatible ROMs (lowercase)
    linux_asset_pattern: str  # Glob pattern for Linux release asset
    executable_name: str  # Name of the main executable

    # Optional fields
    launch_args: str = ""  # Additional launch arguments
    steamgriddb_name: str | None = None  # Override name for SteamGridDB artwork lookup
    texture_pack_repo: str | None = None  # GitHub repo for HD texture pack (legacy)
    enhancements: tuple[Enhancement, ...] = ()  # Available enhancements for this port


# Ship of Harkinian supported ROM hashes
# Source: https://github.com/HarbourMasters/Shipwright/blob/develop/docs/supportedHashes.json
SOH_HASHES = frozenset(
    {
        # PAL versions
        "328a1f1beba30ce5e178f031662019eb32c5f3b5",  # PAL 1.0
        "cfbb98d392e4a9d39da8285d10cbef3974c2f012",  # PAL 1.1
        "0227d7c0074f2d0ac935631990da8ec5914597b4",  # PAL GC
        "f46239439f59a2a594ef83cf68ef65043b1bffe2",  # PAL MQ
        "cee6bc3c2a634b41728f2af8da54d9bf8cc14099",  # PAL GC Debug
        "079b855b943d6ad8bd1eb026c0ed169ecbdac7da",  # PAL MQ Debug
        "50bebedad9e0f10746a52b07239e47fa6c284d03",  # PAL MQ Debug
        "cfecfdc58d650e71a200c81f033de4e6d617a9f6",  # PAL MQ Debug
        # NTSC US versions
        "ad69c91157f6705e8ab06c79fe08aad47bb57ba7",  # NTSC 1.0 US
        "d3ecb253776cd847a5aa63d859d8c89a2f37b364",  # NTSC 1.1 US
        "41b3bdc48d98c48529219919015a1af22f5057c2",  # NTSC 1.2 US
        "b82710ba2bd3b4c6ee8aa1a7e9acf787dfc72e9b",  # NTSC GC US
        "8b5d13aac69bfbf989861cfdc50b1d840945fc1d",  # NTSC MQ US
        # NTSC JP versions
        "c892bbda3993e66bd0d56a10ecd30b1ee612210f",  # NTSC 1.0 JP
        "dbfc81f655187dc6fefd93fa6798face770d579d",  # NTSC 1.1 JP
        "fa5f5942b27480d60243c2d52c0e93e26b9e6b86",  # NTSC 1.2 JP
        "0769c84615422d60f16925cd859593cdfa597f84",  # NTSC GC JP
        "2ce2d1a9f0534c9cd9fa04ea5317b80da21e5e73",  # NTSC GC JP CE
        "dd14e143c4275861fe93ea79d0c02e36ae8c6c2f",  # NTSC MQ JP
    }
)

# Registry of all supported ports
PORTS: dict[str, Port] = {
    "soh": Port(
        id="soh",
        name="Ship of Harkinian",
        type=PortType.HARBOUR_MASTERS,
        github_repo="HarbourMasters/Shipwright",
        system="n64",
        rom_search_name="Zelda - Ocarina of Time",
        required_hashes=SOH_HASHES,
        linux_asset_pattern="*Linux*.zip",
        executable_name="soh.elf",
        steamgriddb_name="The Legend of Zelda: Ocarina of Time",
        texture_pack_repo="GhostlyDark/OoT-Reloaded-SoH",
        enhancements=(
            Enhancement(
                id="oot-reloaded",
                name="OoT Reloaded HD Textures",
                repo="GhostlyDark/OoT-Reloaded-SoH",
                asset_pattern="*.o2r",
                install_subdir="mods",
            ),
        ),
    ),
    "2ship": Port(
        id="2ship",
        name="2Ship2Harkinian",
        type=PortType.HARBOUR_MASTERS,
        github_repo="HarbourMasters/2ship2harkinian",
        system="n64",
        rom_search_name="Zelda - Majora's Mask",
        # TODO: Add actual hashes from supportedHashes.json
        required_hashes=frozenset(),
        linux_asset_pattern="*Linux*.zip",
        executable_name="2s2h.elf",
        steamgriddb_name="The Legend of Zelda: Majora's Mask",
        texture_pack_repo="GhostlyDark/MM-Reloaded-2S2H",
        enhancements=(
            Enhancement(
                id="mm-reloaded",
                name="MM Reloaded HD Textures",
                repo="GhostlyDark/MM-Reloaded-2S2H",
                asset_pattern="*.o2r",
                install_subdir="mods",
            ),
        ),
    ),
    "spaghettikart": Port(
        id="spaghettikart",
        name="SpaghettiKart",
        type=PortType.HARBOUR_MASTERS,
        github_repo="HarbourMasters/SpaghettiKart",
        system="n64",
        rom_search_name="Mario Kart 64 (USA)",
        required_hashes=frozenset({"579c48e211ae952530ffc8738709f078d5dd215e"}),
        linux_asset_pattern="*Linux*.zip",
        executable_name="spaghettikart.elf",
        steamgriddb_name="Mario Kart 64",
        texture_pack_repo="GhostlyDark/MK64-Reloaded-SK",
        enhancements=(
            Enhancement(
                id="mk64-reloaded",
                name="MK64 Reloaded HD Textures",
                repo="GhostlyDark/MK64-Reloaded-SK",
                asset_pattern="*.o2r",
                install_subdir="mods",
            ),
        ),
    ),
    "starship": Port(
        id="starship",
        name="Starship",
        type=PortType.HARBOUR_MASTERS,
        github_repo="HarbourMasters/Starship",
        system="n64",
        rom_search_name="Star Fox 64 (USA)",
        required_hashes=frozenset(
            {
                "d8b1088520f7c5f81433292a9258c1184afa1457",  # US 1.0
                "09f0d105f476b00efa5303a3ebc42e60a7753b7a",  # US 1.1
            }
        ),
        linux_asset_pattern="*Linux*.zip",
        executable_name="starship.elf",
        steamgriddb_name="Star Fox 64",
    ),
    "opengoal-jak1": Port(
        id="opengoal-jak1",
        name="OpenGOAL: Jak and Daxter",
        type=PortType.OPENGOAL,
        github_repo="open-goal/jak-project",
        system="ps2",
        rom_search_name="Jak and Daxter",
        required_hashes=frozenset(),  # TODO: Add actual hashes
        linux_asset_pattern="opengoal-linux*.tar.gz",
        executable_name="gk",
        launch_args="-g jak1",
        steamgriddb_name="Jak and Daxter: The Precursor Legacy",
    ),
    "opengoal-jak2": Port(
        id="opengoal-jak2",
        name="OpenGOAL: Jak II",
        type=PortType.OPENGOAL,
        github_repo="open-goal/jak-project",
        system="ps2",
        rom_search_name="Jak II",
        required_hashes=frozenset(),  # TODO: Add actual hashes
        linux_asset_pattern="opengoal-linux*.tar.gz",
        executable_name="gk",
        launch_args="-g jak2",
        steamgriddb_name="Jak II",
        texture_pack_repo="Melechtna/OpenGOAL-Jak2-HD-Texture-Pack",
        enhancements=(
            Enhancement(
                id="jak2-hd",
                name="Jak II HD Texture Pack",
                repo="Melechtna/OpenGOAL-Jak2-HD-Texture-Pack",
                asset_pattern="*.zip",
                install_subdir="texture_replacements",
            ),
        ),
    ),
    "perfect-dark": Port(
        id="perfect-dark",
        name="Perfect Dark",
        type=PortType.DIRECT_PORT,
        github_repo="fgsfdsfgs/perfect_dark",
        system="n64",
        rom_search_name="Perfect Dark (USA)",
        required_hashes=frozenset(),  # TODO: Add actual hashes
        linux_asset_pattern="pd-*-linux*.tar.gz",
        executable_name="pd",
        steamgriddb_name="Perfect Dark",
    ),
}


def get_port(port_id: str) -> Port | None:
    """Get a port by ID.

    Args:
        port_id: The port identifier.

    Returns:
        The Port if found, None otherwise.
    """
    return PORTS.get(port_id)


def list_ports() -> list[Port]:
    """List all available ports.

    Returns:
        List of all Port definitions.
    """
    return list(PORTS.values())
