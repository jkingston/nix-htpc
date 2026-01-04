{ config, pkgs, lib, ... }:
let
  # RetroArch with cores for 2D consoles
  retroarchWithCores = pkgs.retroarch.withCores (cores: with cores; [
    snes9x           # SNES
    bsnes            # SNES (high accuracy)
    mgba             # GBA
    mupen64plus      # N64
    genesis-plus-gx  # Genesis/Mega Drive/Sega CD
    mesen            # NES (high accuracy)
    beetle-psx-hw    # PS1 (alternative to DuckStation)
  ]);

  # Emulator wrapper scripts for Steam launching
  # These ensure: fullscreen, batch mode (exit after game), clean return to Steam

  dolphinWrapper = pkgs.writeShellScriptBin "dolphin-wrapper" ''
    # Dolphin (GameCube/Wii) wrapper for Steam
    # -b = batch mode (exit when game closes)
    # -e = execute game
    exec ${pkgs.dolphin-emu}/bin/dolphin-emu -b -e "$@"
  '';

  pcsx2Wrapper = pkgs.writeShellScriptBin "pcsx2-wrapper" ''
    # PCSX2 (PS2) wrapper for Steam
    # -fullscreen = start fullscreen
    # -batch = exit when game closes
    exec ${pkgs.pcsx2}/bin/pcsx2-qt -fullscreen -batch -- "$@"
  '';

  duckstationWrapper = pkgs.writeShellScriptBin "duckstation-wrapper" ''
    # DuckStation (PS1) wrapper for Steam
    # -fullscreen = start fullscreen
    # -batch = exit when game closes
    exec ${pkgs.duckstation}/bin/duckstation-qt -fullscreen -batch -- "$@"
  '';

  # RetroArch wrapper - takes core name as first arg, then ROM path
  retroarchWrapper = pkgs.writeShellScriptBin "retroarch-wrapper" ''
    # RetroArch wrapper for Steam
    # Usage: retroarch-wrapper <core-name> <rom-path>
    # Example: retroarch-wrapper snes9x "/path/to/game.sfc"
    CORE_NAME="$1"
    shift
    CORE_PATH="${retroarchWithCores}/lib/retroarch/cores/''${CORE_NAME}_libretro.so"
    if [ ! -f "$CORE_PATH" ]; then
      echo "Core not found: $CORE_PATH" >&2
      exit 1
    fi
    exec ${retroarchWithCores}/bin/retroarch -f -L "$CORE_PATH" "$@"
  '';

in
{
  # Steam with Proton support
  programs.steam = {
    enable = true;
    remotePlay.openFirewall = true;
    dedicatedServer.openFirewall = true;
    gamescopeSession.enable = true;
    # Proton-GE for better game compatibility
    extraCompatPackages = with pkgs; [ proton-ge-bin ];
  };

  # 32-bit graphics support (required for most Steam games on AMD)
  hardware.graphics = {
    enable = true;
    enable32Bit = true;
  };

  # Gaming packages
  environment.systemPackages = with pkgs; [
    # Display/compositor
    gamescope
    mangohud

    # Standalone emulators (3D consoles - better performance)
    dolphin-emu     # GameCube / Wii
    pcsx2           # PS2
    duckstation     # PS1

    # RetroArch (2D consoles)
    retroarchWithCores

    # Emulator wrappers for Steam launching
    dolphinWrapper
    pcsx2Wrapper
    duckstationWrapper
    retroarchWrapper

    # ROM management
    steam-rom-manager
  ];

  # Gamemode for automatic performance optimization
  programs.gamemode = {
    enable = true;
    settings = {
      general = {
        renice = 10;
      };
      gpu = {
        apply_gpu_optimisations = "accept-responsibility";
        gpu_device = 0;
      };
    };
  };

  # Add htpc user to gamemode group
  users.users.htpc.extraGroups = [ "gamemode" ];

  # Additional firewall rules for Steam streaming
  networking.firewall = {
    allowedTCPPorts = [ 27036 27037 ];
    allowedUDPPorts = [ 27031 27036 ];
  };
}
