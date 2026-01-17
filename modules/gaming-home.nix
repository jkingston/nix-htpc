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
  # Optimized for AMD Ryzen 5 5560U iGPU + 4K TV + controller-only operation

  # RetroArch: Full config with hotkeys and AMD optimizations
  xdg.configFile."retroarch/retroarch.cfg".text = ''
    # Directories
    system_directory = "${biosDir}"
    savefile_directory = "${emulationDir}/saves"
    savestate_directory = "${emulationDir}/states"

    # Video - optimized for AMD
    video_fullscreen = "true"
    video_driver = "vulkan"
    video_smooth = "false"
    video_scale_integer = "false"
    aspect_ratio_index = "22"

    # HOTKEYS - L3+R3 opens menu (combo ID 2)
    # Safe combo that doesn't conflict with any games
    input_menu_toggle_gamepad_combo = "2"

    # Menu navigation
    menu_unified_controls = "true"
  '';

  # DuckStation: Full config with hotkeys, PGXP, Vulkan
  xdg.configFile."duckstation/settings.ini".text = ''
    [BIOS]
    SearchDirectory = ${biosDir}

    [Main]
    StartFullscreen = true

    [Display]
    Fullscreen = true

    [GPU]
    Renderer = Vulkan
    ResolutionScale = 4
    TextureFilter = Bilinear

    [PGXP]
    Enable = true
    CullingTolerance = true
    TextureCorrection = true

    [Hotkeys]
    # Guide button opens pause menu (controller-friendly)
    OpenPauseMenu = SDL-0/Guide
  '';

  # PCSX2: Full config with Vulkan and speedhacks
  # Note: PCSX2 hotkeys are mostly keyboard-only, but pause works with controller
  xdg.configFile."PCSX2/inis/PCSX2.ini".text = ''
    [Folders]
    Bios = ${biosDir}
    Savestates = ${emulationDir}/states/ps2
    MemoryCards = ${emulationDir}/saves/ps2

    [UI]
    StartFullscreen = true

    [EmuCore/GS]
    Renderer = 14
    upscale_multiplier = 2
    texture_filtering = 2
    dithering_ps2 = 2

    [EmuCore/Speedhacks]
    EECycleRate = 0
    EECycleSkip = 0
    fastCDVD = false
    IntcStat = true
    WaitLoop = true
    vuFlagHack = true
    vuThread = true
  '';

  # Dolphin: Vulkan backend and resolution config
  xdg.configFile."dolphin-emu/Dolphin.ini".text = ''
    [Display]
    Fullscreen = True

    [General]
    NANDRootPath = ${emulationDir}/dolphin/nand

    [Core]
    GFXBackend = Vulkan
  '';

  # Dolphin: Graphics settings for 4K
  xdg.configFile."dolphin-emu/GFX.ini".text = ''
    [Settings]
    AspectRatio = 0
    wideScreenHack = False
    ShaderCompilationMode = 2
    WaitForShadersBeforeStarting = False
    EFBScale = 2
    MaxAnisotropy = 2

    [Hacks]
    EFBAccessEnable = True
    EFBAccessDeferInvalidation = False
    EFBEmulateFormatChanges = False
    BBoxEnable = False
    VertexRounding = False
  '';

  # Dolphin: Hotkeys config
  xdg.configFile."dolphin-emu/Hotkeys.ini".text = ''
    [Hotkeys]
    [Hotkeys1]
    Device = XInput2/0/Virtual core pointer
  '';

  # PPSSPP: Vulkan and resolution for 4K
  xdg.configFile."ppsspp/PSP/SYSTEM/ppsspp.ini".text = ''
    [Graphics]
    Backend = 3
    InternalResolution = 4
    TextureFiltering = 1
    TextureScalingLevel = 4
    TexScalingType = 3
    Deposterize = true

    [General]
    Fullscreen = True
  '';

}
