# pier

HTPC game management tool for NixOS. Manages ROMs, native game ports, and Steam integration.

## Features

- **Native Game Ports**: One-command installation of PC ports like Ship of Harkinian, OpenGOAL, and more
- **ROM Management**: Browse and download ROMs from myrient
- **BIOS Management**: Check and download emulator BIOS files
- **Steam Integration**: Automatically create Steam shortcuts with artwork
- **HD Textures**: Optional high-definition texture packs for supported ports
- **TUI Interface**: Full-screen terminal UI for easy navigation

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- NixOS (for full Steam integration via `steam-run`)

## Installation

```bash
cd pier
uv sync
```

## Quick Start

```bash
# Launch the TUI
uv run pier

# Or use CLI commands directly
uv run pier list                    # List available ports
uv run pier install soh             # Install Ship of Harkinian
uv run pier bios check              # Check BIOS file status
```

## CLI Reference

### General

| Command | Description |
|---------|-------------|
| `pier` | Launch TUI interface |
| `pier list` | List available and installed ports |
| `pier install <port>` | Install a native game port |
| `pier update [port]` | Check for and apply updates |

### ROM Management

| Command | Description |
|---------|-------------|
| `pier roms list <system>` | List ROMs available from myrient |
| `pier roms search <system> <query>` | Search for ROMs |
| `pier roms download <system> <file>` | Download a ROM |

### Steam Integration

| Command | Description |
|---------|-------------|
| `pier steam sync` | Sync all linked games to Steam |
| `pier steam link <id>` | Mark a game for Steam linking |
| `pier steam unlink <id>` | Remove a game from Steam |

### BIOS Management

| Command | Description |
|---------|-------------|
| `pier bios check` | Show status of all BIOS files |
| `pier bios list [system]` | List available BIOS files |
| `pier bios download [file]` | Download BIOS files (recommended by default) |
| `pier bios download --all` | Download all BIOS files |

### Configuration

| Command | Description |
|---------|-------------|
| `pier config get [key]` | Show configuration (all if no key) |
| `pier config set <key> <value>` | Set a configuration value |
| `pier config path` | Show configuration file paths |

## Available Ports

| ID | Name | Game |
|----|------|------|
| `soh` | Ship of Harkinian | Ocarina of Time |
| `2ship` | 2Ship2Harkinian | Majora's Mask |
| `spaghettikart` | SpaghettiKart | Mario Kart 64 |
| `starship` | Starship | Star Fox 64 |
| `sm64coopdx` | SM64 Coop DX | Super Mario 64 (Multiplayer) |
| `perfect-dark` | Perfect Dark | Perfect Dark |
| `opengoal-jak1` | OpenGOAL - Jak 1 | Jak and Daxter |
| `opengoal-jak2` | OpenGOAL - Jak II | Jak II |
| `opengoal-jak3` | OpenGOAL - Jak 3 | Jak 3 |

## Supported ROM Systems

| ID | System |
|----|--------|
| `n64` | Nintendo 64 |
| `snes` | Super Nintendo |
| `nes` | Nintendo Entertainment System |
| `gba` | Game Boy Advance |
| `genesis` | Sega Genesis / Mega Drive |
| `ps1` | PlayStation |
| `ps2` | PlayStation 2 |
| `gc` | GameCube |
| `wii` | Wii |

## Configuration

Configuration is stored in `~/Emulation/.pier/config.json`.

| Key | Default | Description |
|-----|---------|-------------|
| `steamgriddb_api_key` | `null` | API key for SteamGridDB artwork |
| `auto_fetch_artwork` | `true` | Automatically fetch artwork on install |
| `auto_add_to_steam` | `true` | Automatically add shortcuts to Steam |
| `install_hd_textures` | `true` | Install HD texture packs when available |

To get a SteamGridDB API key, visit [steamgriddb.com](https://www.steamgriddb.com/profile/preferences/api).

## Directory Structure

```
~/Emulation/
├── .pier/
│   ├── config.json    # User configuration
│   └── library.json   # Installed games and Steam links
├── roms/
│   ├── n64/           # Nintendo 64 ROMs
│   ├── ps1/           # PlayStation ROMs
│   └── ...
├── ports/
│   ├── soh/           # Ship of Harkinian
│   ├── 2ship/         # 2Ship2Harkinian
│   └── ...
└── bios/
    ├── scph5501.bin   # PS1 BIOS
    └── ...
```

## TUI Navigation

- **Arrow keys** / **j/k**: Navigate lists
- **Enter** / **Space**: Select / Toggle
- **Tab**: Switch between panels
- **Escape**: Go back
- **q**: Quit

## Install Options

```bash
# Install with options
pier install soh --no-mods      # Skip HD texture pack
pier install soh --no-steam     # Don't add to Steam
pier install soh --no-artwork   # Don't fetch artwork
```

## License

MIT
