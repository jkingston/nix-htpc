{ config, pkgs, lib, ... }:
let
  # Base paths
  steamDir = "${config.home.homeDirectory}/.local/share/Steam";
  emulationDir = "${config.home.homeDirectory}/Emulation";
  romsDir = "${emulationDir}/roms";
  biosDir = "${emulationDir}/bios";

  # ROM directory structure (can be symlinks to NAS in future)
  romSystems = [
    "nes"
    "snes"
    "n64"
    "gba"
    "genesis"
    "ps1"
    "ps2"
    "gc"      # GameCube
    "wii"
  ];

  # Helper to create a complete SRM parser config with all required fields
  mkGlobParser = { title, category, romDir, glob, executablePath, executableArgs }: {
    parserType = "Glob";
    configTitle = title;
    steamDirectory = steamDir;
    romDirectory = romDir;
    steamCategories = [ category ];
    executableArgs = executableArgs;
    executableModifier = "\"\${exePath}\"";
    startInDirectory = "";
    titleModifier = "\${fuzzyTitle}";
    imageProviders = [ "sgdb" ];
    onlineImageQueries = "\${\${fuzzyTitle}}";
    imagePool = "\${fuzzyTitle}";
    disabled = false;
    userAccounts = { specifiedAccounts = [ "Global" ]; };
    executable = {
      path = executablePath;
      shortcutPassthrough = false;
      appendArgsToExecutable = true;
    };
    parserInputs = {
      inherit glob;
    };
    titleFromVariable = {
      limitToGroups = "";
      caseInsensitiveVariables = false;
      skipFileIfVariableWasNotFound = false;
      tryToMatchTitle = false;
    };
    fuzzyMatch = {
      removeCharacters = true;
      removeBrackets = true;
      replaceDiacritics = true;
    };
    controllers = {};
    imageProviderAPIs = {
      sgdb = { nsfw = false; humor = false; };
    };
    defaultImage = {};
    localImages = {};
  };

  mkManualParser = { title, category, manifestsDir }: {
    parserType = "Manual";
    configTitle = title;
    steamDirectory = steamDir;
    steamCategories = [ category ];
    executableModifier = "\"\${exePath}\"";
    startInDirectory = "";
    titleModifier = "\${fuzzyTitle}";
    imageProviders = [ "sgdb" ];
    onlineImageQueries = "\${\${fuzzyTitle}}";
    imagePool = "\${fuzzyTitle}";
    disabled = false;
    userAccounts = { specifiedAccounts = [ "Global" ]; };
    parserInputs = {
      manualManifests = manifestsDir;
    };
    titleFromVariable = {
      limitToGroups = "";
      caseInsensitiveVariables = false;
      skipFileIfVariableWasNotFound = false;
      tryToMatchTitle = false;
    };
    fuzzyMatch = {
      removeCharacters = true;
      removeBrackets = true;
      replaceDiacritics = true;
    };
    controllers = {};
    imageProviderAPIs = {
      sgdb = { nsfw = false; humor = false; };
    };
    defaultImage = {};
    localImages = {};
  };

  # Steam ROM Manager parser configurations
  srmParsers = [
    (mkGlobParser {
      title = "Nintendo - SNES";
      category = "Emulation";
      romDir = "${romsDir}/snes";
      glob = "**/*.{sfc,smc,zip}";
      executablePath = "retroarch-wrapper";
      executableArgs = "snes9x \"\${filePath}\"";
    })
    (mkGlobParser {
      title = "Nintendo - NES";
      category = "Emulation";
      romDir = "${romsDir}/nes";
      glob = "**/*.{nes,zip}";
      executablePath = "retroarch-wrapper";
      executableArgs = "mesen \"\${filePath}\"";
    })
    (mkGlobParser {
      title = "Nintendo - N64";
      category = "Emulation";
      romDir = "${romsDir}/n64";
      glob = "**/*.{n64,z64,v64,zip}";
      executablePath = "retroarch-wrapper";
      executableArgs = "mupen64plus_next \"\${filePath}\"";
    })
    (mkGlobParser {
      title = "Nintendo - GBA";
      category = "Emulation";
      romDir = "${romsDir}/gba";
      glob = "**/*.{gba,zip}";
      executablePath = "retroarch-wrapper";
      executableArgs = "mgba \"\${filePath}\"";
    })
    (mkGlobParser {
      title = "Sega - Genesis";
      category = "Emulation";
      romDir = "${romsDir}/genesis";
      glob = "**/*.{md,bin,gen,zip}";
      executablePath = "retroarch-wrapper";
      executableArgs = "genesis_plus_gx \"\${filePath}\"";
    })
    (mkGlobParser {
      title = "Sony - PS1";
      category = "Emulation";
      romDir = "${romsDir}/ps1";
      glob = "**/*.{bin,cue,iso,chd}";
      executablePath = "duckstation-wrapper";
      executableArgs = "\"\${filePath}\"";
    })
    (mkGlobParser {
      title = "Sony - PS2";
      category = "Emulation";
      romDir = "${romsDir}/ps2";
      glob = "**/*.{iso,chd,cso}";
      executablePath = "pcsx2-wrapper";
      executableArgs = "\"\${filePath}\"";
    })
    (mkGlobParser {
      title = "Nintendo - GameCube";
      category = "Emulation";
      romDir = "${romsDir}/gc";
      glob = "**/*.{iso,gcm,rvz}";
      executablePath = "dolphin-wrapper";
      executableArgs = "\"\${filePath}\"";
    })
    (mkGlobParser {
      title = "Nintendo - Wii";
      category = "Emulation";
      romDir = "${romsDir}/wii";
      glob = "**/*.{iso,wbfs,rvz}";
      executablePath = "dolphin-wrapper";
      executableArgs = "\"\${filePath}\"";
    })
    # Manual parser for HTPC apps (Kodi launcher)
    (mkManualParser {
      title = "HTPC Apps";
      category = "HTPC";
      manifestsDir = "${config.xdg.configHome}/steam-rom-manager/manifests";
    })
  ];

  # Kodi manifest for SRM Manual parser
  # Format: JSON array of app definitions (per SRM issue #723)
  kodiManifest = [
    {
      title = "Kodi";
      target = "/run/current-system/sw/bin/kodi-launcher";
      startIn = "/tmp";
      launchOptions = "";
    }
  ];

  # Python with dependencies for Steam shortcut management
  pythonWithDeps = pkgs.python3.withPackages (ps: [ ps.vdf ps.requests ]);

  # Comprehensive ROM sync script - scans ROMs and creates Steam shortcuts
  # Optionally fetches artwork from SteamGridDB if API key is available
  syncRomShortcuts = pkgs.writeScriptBin "sync-rom-shortcuts" ''
    #!${pythonWithDeps}/bin/python3
    """
    Sync ROM files to Steam shortcuts with optional SteamGridDB artwork.

    Scans configured ROM directories, creates Steam shortcuts for each game,
    and optionally downloads artwork from SteamGridDB.

    API key: Place your SteamGridDB API key in ~/.config/steamgriddb/api_key
    Get one free at: https://www.steamgriddb.com/profile/preferences/api
    """
    import sys
    import os
    import re
    import json
    import binascii
    import hashlib
    from pathlib import Path
    from typing import Optional
    from urllib.parse import quote

    import vdf
    import requests

    # ROM system definitions - extensions based on libretro docs and emulator documentation
    # Sources:
    #   - https://docs.libretro.com/library/mesen/
    #   - https://docs.libretro.com/library/snes9x/
    #   - https://docs.libretro.com/library/mupen64plus/
    #   - https://docs.libretro.com/library/mgba/
    #   - https://docs.libretro.com/library/genesis_plus_gx/
    #   - https://github.com/stenzek/duckstation
    #   - https://pcsx2.net
    #   - https://dolphin-emu.org/docs/faq/
    ROM_SYSTEMS = [
        {
            "system": "nes",
            "name": "NES",
            "exts": ["nes", "fds", "unf", "unif", "zip", "7z"],
            "wrapper": "retroarch-wrapper",
            "args": "mesen",
            "tag": "NES",
        },
        {
            "system": "snes",
            "name": "SNES",
            "exts": ["smc", "sfc", "swc", "fig", "bs", "st", "zip", "7z"],
            "wrapper": "retroarch-wrapper",
            "args": "snes9x",
            "tag": "SNES",
        },
        {
            "system": "n64",
            "name": "N64",
            "exts": ["n64", "z64", "v64", "ndd", "zip", "7z"],
            "wrapper": "retroarch-wrapper",
            "args": "mupen64plus_next",
            "tag": "N64",
        },
        {
            "system": "gba",
            "name": "GBA",
            "exts": ["gb", "gbc", "gba", "zip", "7z"],
            "wrapper": "retroarch-wrapper",
            "args": "mgba",
            "tag": "GBA",
        },
        {
            "system": "genesis",
            "name": "Genesis",
            "exts": ["md", "smd", "gen", "bin", "sg", "sms", "gg", "cue", "iso", "chd", "68k", "sgd", "zip", "7z"],
            "wrapper": "retroarch-wrapper",
            "args": "genesis_plus_gx",
            "tag": "Genesis",
        },
        {
            "system": "ps1",
            "name": "PS1",
            "exts": ["cue", "iso", "img", "chd", "ecm", "mds", "mdf", "pbp", "m3u"],
            "wrapper": "duckstation-wrapper",
            "args": "",
            "tag": "PS1",
        },
        {
            "system": "ps2",
            "name": "PS2",
            "exts": ["iso", "chd", "cso", "cue", "mdf", "mds", "gz"],
            "wrapper": "pcsx2-wrapper",
            "args": "",
            "tag": "PS2",
        },
        {
            "system": "gc",
            "name": "GameCube",
            "exts": ["iso", "gcm", "gcz", "rvz", "ciso", "wia", "dol", "elf", "tgc"],
            "wrapper": "dolphin-wrapper",
            "args": "",
            "tag": "GameCube",
        },
        {
            "system": "wii",
            "name": "Wii",
            "exts": ["iso", "wbfs", "gcz", "rvz", "ciso", "wia", "wad", "dol", "elf"],
            "wrapper": "dolphin-wrapper",
            "args": "",
            "tag": "Wii",
        },
    ]

    ROMS_DIR = Path.home() / "Emulation" / "roms"
    SGDB_API_KEY_FILE = Path.home() / ".config" / "steamgriddb" / "api_key"
    SGDB_BASE_URL = "https://www.steamgriddb.com/api/v2"


    def generate_appid(exe: str, app_name: str) -> int:
        """Generate Steam shortcut appid from exe path and name."""
        key = exe + app_name
        crc = binascii.crc32(key.encode('utf-8')) & 0xffffffff
        appid = (crc | 0x80000000) ^ 0xFFFFFFFF
        if appid > 0x7FFFFFFF:
            appid -= 0x100000000
        return appid


    def generate_grid_id(exe: str, app_name: str) -> int:
        """Generate the unsigned appid used for grid image filenames."""
        key = exe + app_name
        crc = binascii.crc32(key.encode('utf-8')) & 0xffffffff
        return (crc | 0x80000000)


    def find_steam_userdata() -> Path:
        """Find Steam userdata directory."""
        steam_dir = Path.home() / ".local/share/Steam/userdata"
        if not steam_dir.exists():
            raise FileNotFoundError(f"Steam userdata not found: {steam_dir}")

        user_ids = [d for d in steam_dir.iterdir() if d.is_dir() and d.name.isdigit()]
        if not user_ids:
            raise FileNotFoundError("No Steam user found in userdata")

        return user_ids[0]


    def load_shortcuts(shortcuts_path: Path) -> dict:
        """Load existing shortcuts.vdf or return empty structure."""
        if shortcuts_path.exists():
            try:
                with open(shortcuts_path, 'rb') as f:
                    return vdf.binary_load(f)
            except Exception as e:
                print(f"Warning: Could not read existing shortcuts.vdf: {e}", file=sys.stderr)
        return {'shortcuts': {}}


    def save_shortcuts(shortcuts_path: Path, data: dict):
        """Save shortcuts.vdf in binary format."""
        shortcuts_path.parent.mkdir(parents=True, exist_ok=True)
        with open(shortcuts_path, 'wb') as f:
            vdf.binary_dump(data, f)


    def clean_title(filename: str) -> str:
        """Extract clean game title from ROM filename."""
        # Remove extension
        name = Path(filename).stem

        # Remove common patterns: (USA), [!], (Rev 1), (En,Fr), etc.
        patterns = [
            r'\s*\([^)]*\)',      # (anything in parens)
            r'\s*\[[^\]]*\]',     # [anything in brackets]
            r'\s*\{[^}]*\}',      # {anything in braces}
            r'\s+-\s*$',          # trailing dash
            r'\s+$',              # trailing whitespace
        ]
        for pattern in patterns:
            name = re.sub(pattern, "", name)

        return name.strip()


    def get_sgdb_api_key() -> Optional[str]:
        """Load SteamGridDB API key from file if available."""
        if SGDB_API_KEY_FILE.exists():
            try:
                key = SGDB_API_KEY_FILE.read_text().strip()
                if key:
                    return key
            except Exception:
                pass
        return None


    def search_sgdb_game(api_key: str, title: str) -> Optional[int]:
        """Search SteamGridDB for a game by title, return game ID."""
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = requests.get(
                f"{SGDB_BASE_URL}/search/autocomplete/{quote(title)}",
                headers=headers,
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") and data.get("data"):
                    return data["data"][0]["id"]
        except Exception as e:
            print(f"  SGDB search failed for '{title}': {e}", file=sys.stderr)
        return None


    def download_sgdb_grid(api_key: str, game_id: int, grid_path: Path, grid_id: int):
        """Download grid image from SteamGridDB."""
        try:
            headers = {"Authorization": f"Bearer {api_key}"}

            # Get available grids (600x900 vertical format)
            resp = requests.get(
                f"{SGDB_BASE_URL}/grids/game/{game_id}?dimensions=600x900",
                headers=headers,
                timeout=10
            )
            if resp.status_code != 200:
                return

            data = resp.json()
            if not data.get("success") or not data.get("data"):
                return

            # Download first available grid
            image_url = data["data"][0]["url"]
            ext = Path(image_url).suffix or ".png"

            img_resp = requests.get(image_url, timeout=30)
            if img_resp.status_code == 200:
                grid_path.mkdir(parents=True, exist_ok=True)
                img_file = grid_path / f"{grid_id}p{ext}"
                img_file.write_bytes(img_resp.content)
                print(f"  Downloaded grid artwork")

        except Exception as e:
            print(f"  Grid download failed: {e}", file=sys.stderr)


    def download_sgdb_hero(api_key: str, game_id: int, grid_path: Path, grid_id: int):
        """Download hero image from SteamGridDB."""
        try:
            headers = {"Authorization": f"Bearer {api_key}"}

            resp = requests.get(
                f"{SGDB_BASE_URL}/heroes/game/{game_id}",
                headers=headers,
                timeout=10
            )
            if resp.status_code != 200:
                return

            data = resp.json()
            if not data.get("success") or not data.get("data"):
                return

            image_url = data["data"][0]["url"]
            ext = Path(image_url).suffix or ".png"

            img_resp = requests.get(image_url, timeout=30)
            if img_resp.status_code == 200:
                grid_path.mkdir(parents=True, exist_ok=True)
                img_file = grid_path / f"{grid_id}_hero{ext}"
                img_file.write_bytes(img_resp.content)
                print(f"  Downloaded hero artwork")

        except Exception as e:
            print(f"  Hero download failed: {e}", file=sys.stderr)


    def scan_roms(system_def: dict) -> list:
        """Scan ROM directory for files matching system extensions."""
        system_dir = ROMS_DIR / system_def["system"]
        if not system_dir.exists():
            return []

        roms = []
        exts = system_def["exts"]

        for ext in exts:
            # Skip bin for PS1/PS2/Genesis to avoid companion files
            # (bin files are usually paired with cue files)
            if ext == "bin" and system_def["system"] in ["ps1", "ps2"]:
                continue

            for rom_file in system_dir.rglob(f"*.{ext}"):
                # Skip if there's a corresponding .cue file (prefer cue over bin)
                if ext == "bin":
                    cue_file = rom_file.with_suffix(".cue")
                    if cue_file.exists():
                        continue

                roms.append(rom_file)

        return sorted(set(roms))


    def build_launch_command(system_def: dict, rom_path: Path) -> tuple:
        """Build the executable and start directory for a ROM."""
        wrapper = system_def["wrapper"]
        args = system_def["args"]

        if args:
            exe = f"{wrapper} {args} \"{rom_path}\""
        else:
            exe = f"{wrapper} \"{rom_path}\""

        return (wrapper, str(rom_path.parent))


    def add_shortcut(data: dict, app_name: str, exe: str, start_dir: str, launch_opts: str, tags: list) -> tuple:
        """Add or update a shortcut entry. Returns (data, grid_id) for artwork."""
        shortcuts = data.get('shortcuts', {})

        # Exe is stored with quotes in shortcuts.vdf, and CRC must use the same string
        quoted_exe = f'"{exe}"'
        appid = generate_appid(quoted_exe, app_name)
        grid_id = generate_grid_id(quoted_exe, app_name)

        # Find existing entry
        existing_idx = None
        for idx, entry in shortcuts.items():
            if entry.get('appid') == appid or entry.get('AppName') == app_name:
                existing_idx = idx
                break

        if existing_idx is not None:
            idx = existing_idx
        else:
            existing_indices = [int(k) for k in shortcuts.keys() if k.isdigit()]
            idx = str(max(existing_indices) + 1) if existing_indices else '0'

        shortcut = {
            'appid': appid,
            'AppName': app_name,
            'Exe': quoted_exe,
            'StartDir': f'"{start_dir}"',
            'icon': "",
            'ShortcutPath': "",
            'LaunchOptions': launch_opts,
            'IsHidden': 0,
            'AllowDesktopConfig': 1,
            'AllowOverlay': 1,
            'OpenVR': 0,
            'Devkit': 0,
            'DevkitGameID': "",
            'DevkitOverrideAppID': 0,
            'LastPlayTime': 0,
            'FlatpakAppID': "",
            'tags': {str(i): tag for i, tag in enumerate(tags)},
        }

        shortcuts[idx] = shortcut
        data['shortcuts'] = shortcuts
        return (data, grid_id)


    def main():
        import argparse
        parser = argparse.ArgumentParser(description="Sync ROMs to Steam shortcuts")
        parser.add_argument("--no-artwork", action="store_true", help="Skip SteamGridDB artwork")
        parser.add_argument("--system", help="Only sync specific system (nes, snes, etc.)")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
        args = parser.parse_args()

        # Check Steam not running
        import subprocess
        result = subprocess.run(["pgrep", "-u", os.environ["USER"], "steam"],
                                capture_output=True)
        if result.returncode == 0:
            print("Error: Steam is running. Please close it first.", file=sys.stderr)
            sys.exit(1)

        try:
            user_dir = find_steam_userdata()
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            print("Has Steam been run at least once?", file=sys.stderr)
            sys.exit(1)

        shortcuts_path = user_dir / "config" / "shortcuts.vdf"
        grid_path = user_dir / "config" / "grid"

        print(f"Steam user: {user_dir.name}")
        print(f"ROMs directory: {ROMS_DIR}")
        print()

        # Load API key
        api_key = None
        if not args.no_artwork:
            api_key = get_sgdb_api_key()
            if api_key:
                print("SteamGridDB API key loaded - artwork will be downloaded")
            else:
                print(f"No SteamGridDB API key found at {SGDB_API_KEY_FILE}")
                print("Skipping artwork (shortcuts will still work)")
            print()

        # Load existing shortcuts
        data = load_shortcuts(shortcuts_path)
        total_added = 0

        # Filter systems if requested
        systems = ROM_SYSTEMS
        if args.system:
            systems = [s for s in ROM_SYSTEMS if s["system"] == args.system]
            if not systems:
                print(f"Unknown system: {args.system}", file=sys.stderr)
                sys.exit(1)

        for system_def in systems:
            roms = scan_roms(system_def)
            if not roms:
                continue

            print(f"{system_def['name']}: {len(roms)} ROMs found")

            for rom_path in roms:
                title = clean_title(rom_path.name)
                wrapper = system_def["wrapper"]
                rom_args = system_def["args"]

                # Build exe string - wrapper is in PATH
                if rom_args:
                    launch_opts = f'{rom_args} "{rom_path}"'
                else:
                    launch_opts = f'"{rom_path}"'

                tags = ["Emulation", system_def["tag"]]

                print(f"  + {title}")

                if args.dry_run:
                    continue

                # Add shortcut
                data, grid_id = add_shortcut(
                    data,
                    app_name=title,
                    exe=wrapper,
                    start_dir=str(rom_path.parent),
                    launch_opts=launch_opts,
                    tags=tags
                )
                total_added += 1

                # Download artwork if API key available
                if api_key:
                    game_id = search_sgdb_game(api_key, title)
                    if game_id:
                        download_sgdb_grid(api_key, game_id, grid_path, grid_id)
                        download_sgdb_hero(api_key, game_id, grid_path, grid_id)

        if not args.dry_run and total_added > 0:
            save_shortcuts(shortcuts_path, data)
            print()
            print(f"Added {total_added} shortcuts to Steam")

        # Always add Kodi launcher
        if not args.dry_run:
            kodi_exe = "/run/current-system/sw/bin/kodi-launcher"
            data, _ = add_shortcut(data, "Kodi", kodi_exe, "/tmp", "", ["HTPC"])
            save_shortcuts(shortcuts_path, data)
            print("Added Kodi shortcut")

        print("Done! Restart Steam to see changes.")


    if __name__ == '__main__':
        main()
  '';

in
{
  # Create ROM directory structure + Kodi favourites
  home.file = builtins.listToAttrs (map (system: {
    name = "Emulation/roms/${system}/.keep";
    value = { text = ""; };
  }) romSystems) // {
    # BIOS directory
    "Emulation/bios/.keep".text = "";

    # SRM config directory marker
    "Emulation/.keep".text = "";

    # Kodi favourites - Steam launcher accessible from Favourites menu
    # Note: Kodi uses ~/.kodi/ not XDG ~/.local/share/kodi/
    ".kodi/userdata/favourites.xml".text = ''
      <favourites>
        <favourite name="Steam" thumb="DefaultAddonProgram.png">System.Exec(/run/current-system/sw/bin/steam-launcher)</favourite>
      </favourites>
    '';
  };

  # Steam ROM Manager config
  # Note: SRM stores config in ~/.config/steam-rom-manager/
  # SRM expects a flat array of parser configs (each parser is top-level element)
  xdg.configFile."steam-rom-manager/userConfigurations.json".text =
    builtins.toJSON srmParsers;

  # Kodi manifest for Manual parser
  xdg.configFile."steam-rom-manager/manifests/htpc.json".text =
    builtins.toJSON kodiManifest;

  # Emulator-specific configs

  # RetroArch: set BIOS directory
  xdg.configFile."retroarch/retroarch.cfg".text = ''
    system_directory = "${biosDir}"
    savefile_directory = "${emulationDir}/saves"
    savestate_directory = "${emulationDir}/states"
    video_fullscreen = "true"
  '';

  # DuckStation: set BIOS directory
  xdg.configFile."duckstation/settings.ini".text = ''
    [BIOS]
    SearchDirectory = ${biosDir}

    [Main]
    StartFullscreen = true

    [Display]
    Fullscreen = true
  '';

  # PCSX2: set BIOS directory
  # PCSX2 uses a different config format, this is the Qt version
  xdg.configFile."PCSX2/inis/PCSX2.ini".text = ''
    [Folders]
    Bios = ${biosDir}
    Savestates = ${emulationDir}/states/ps2
    MemoryCards = ${emulationDir}/saves/ps2

    [UI]
    StartFullscreen = true
  '';

  # Dolphin: basic fullscreen config
  xdg.configFile."dolphin-emu/Dolphin.ini".text = ''
    [Display]
    Fullscreen = True

    [General]
    NANDRootPath = ${emulationDir}/dolphin/nand
  '';

  # User packages
  home.packages = [
    syncRomShortcuts
  ];

  # Systemd user service to sync ROM shortcuts to Steam on boot
  # Runs before Steam starts (HTPC boots to Kodi, not Steam)
  # This bypasses SRM which requires X11 display even in CLI mode
  systemd.user.services.steam-shortcuts-sync = {
    Unit = {
      Description = "Sync ROM shortcuts to Steam";
      After = [ "graphical-session.target" ];
    };
    Service = {
      Type = "oneshot";
      ExecStart = pkgs.writeShellScript "steam-shortcuts-sync" ''
        # Check if Steam userdata exists (Steam has been run at least once)
        STEAM_USERDATA="$HOME/.local/share/Steam/userdata"
        if [ ! -d "$STEAM_USERDATA" ]; then
          echo "Steam userdata not found, skipping (Steam hasn't been run yet)"
          exit 0
        fi

        echo "Syncing ROM shortcuts to Steam..."
        ${syncRomShortcuts}/bin/sync-rom-shortcuts || true
        echo "Done"
      '';
    };
    Install = {
      WantedBy = [ "graphical-session.target" ];
    };
  };

}
