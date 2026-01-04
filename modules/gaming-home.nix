{ config, pkgs, lib, ... }:
let
  # Base paths
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

  # Steam ROM Manager parser configuration
  # Each parser defines how to add ROMs for one system to Steam
  srmParsers = [
    {
      parserType = "Glob";
      configTitle = "Nintendo - SNES";
      steamCategory = "Emulation";
      romDirectory = "${romsDir}/snes";
      glob = "**/*.{sfc,smc,zip}";
      executableArgs = "snes9x \"\${filePath}\"";
      executable = { path = "retroarch-wrapper"; };
    }
    {
      parserType = "Glob";
      configTitle = "Nintendo - NES";
      steamCategory = "Emulation";
      romDirectory = "${romsDir}/nes";
      glob = "**/*.{nes,zip}";
      executableArgs = "mesen \"\${filePath}\"";
      executable = { path = "retroarch-wrapper"; };
    }
    {
      parserType = "Glob";
      configTitle = "Nintendo - N64";
      steamCategory = "Emulation";
      romDirectory = "${romsDir}/n64";
      glob = "**/*.{n64,z64,v64,zip}";
      executableArgs = "mupen64plus_next \"\${filePath}\"";
      executable = { path = "retroarch-wrapper"; };
    }
    {
      parserType = "Glob";
      configTitle = "Nintendo - GBA";
      steamCategory = "Emulation";
      romDirectory = "${romsDir}/gba";
      glob = "**/*.{gba,zip}";
      executableArgs = "mgba \"\${filePath}\"";
      executable = { path = "retroarch-wrapper"; };
    }
    {
      parserType = "Glob";
      configTitle = "Sega - Genesis";
      steamCategory = "Emulation";
      romDirectory = "${romsDir}/genesis";
      glob = "**/*.{md,bin,gen,zip}";
      executableArgs = "genesis_plus_gx \"\${filePath}\"";
      executable = { path = "retroarch-wrapper"; };
    }
    {
      parserType = "Glob";
      configTitle = "Sony - PS1";
      steamCategory = "Emulation";
      romDirectory = "${romsDir}/ps1";
      glob = "**/*.{bin,cue,iso,chd}";
      executableArgs = "\"\${filePath}\"";
      executable = { path = "duckstation-wrapper"; };
    }
    {
      parserType = "Glob";
      configTitle = "Sony - PS2";
      steamCategory = "Emulation";
      romDirectory = "${romsDir}/ps2";
      glob = "**/*.{iso,chd,cso}";
      executableArgs = "\"\${filePath}\"";
      executable = { path = "pcsx2-wrapper"; };
    }
    {
      parserType = "Glob";
      configTitle = "Nintendo - GameCube";
      steamCategory = "Emulation";
      romDirectory = "${romsDir}/gc";
      glob = "**/*.{iso,gcm,rvz}";
      executableArgs = "\"\${filePath}\"";
      executable = { path = "dolphin-wrapper"; };
    }
    {
      parserType = "Glob";
      configTitle = "Nintendo - Wii";
      steamCategory = "Emulation";
      romDirectory = "${romsDir}/wii";
      glob = "**/*.{iso,wbfs,rvz}";
      executableArgs = "\"\${filePath}\"";
      executable = { path = "dolphin-wrapper"; };
    }
  ];

  # Generate SRM userConfigurations.json
  srmConfig = {
    parserType = "Glob";
    version = 15;
    configTitle = "default";
    parsers = srmParsers;
  };

  # Script to sync ROMs to Steam shortcuts
  srmSync = pkgs.writeShellScriptBin "srm-sync" ''
    echo "Syncing ROMs to Steam shortcuts..."
    echo "Make sure Steam is fully closed before running this."
    echo ""

    # Check if Steam is running
    if ${pkgs.procps}/bin/pgrep -u $USER steam > /dev/null; then
      echo "Error: Steam is running. Please close it first."
      exit 1
    fi

    # Run Steam ROM Manager CLI to add shortcuts
    ${pkgs.steam-rom-manager}/bin/steam-rom-manager add

    echo ""
    echo "Done! Start Steam to see your ROM shortcuts."
  '';

  # Script to remove all SRM shortcuts
  srmNuke = pkgs.writeShellScriptBin "srm-nuke" ''
    echo "Removing all Steam ROM Manager shortcuts..."

    if ${pkgs.procps}/bin/pgrep -u $USER steam > /dev/null; then
      echo "Error: Steam is running. Please close it first."
      exit 1
    fi

    ${pkgs.steam-rom-manager}/bin/steam-rom-manager nuke

    echo "Done!"
  '';

in
{
  # Create ROM directory structure
  home.file = builtins.listToAttrs (map (system: {
    name = "Emulation/roms/${system}/.keep";
    value = { text = ""; };
  }) romSystems) // {
    # BIOS directory
    "Emulation/bios/.keep".text = "";

    # SRM config directory marker
    "Emulation/.keep".text = "";
  };

  # Steam ROM Manager config
  # Note: SRM stores config in ~/.config/steam-rom-manager/
  xdg.configFile."steam-rom-manager/userConfigurations.json".text =
    builtins.toJSON [ srmConfig ];

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
    srmSync
    srmNuke
  ];

  # Kodi favourites - Steam launcher accessible from Favourites menu
  xdg.dataFile."kodi/userdata/favourites.xml".text = ''
    <favourites>
      <favourite name="Steam" thumb="DefaultAddonProgram.png">System.Exec(/run/current-system/sw/bin/steam-launcher)</favourite>
    </favourites>
  '';
}
