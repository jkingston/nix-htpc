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
    # --video_backend=Vulkan = critical for AMD iGPU (OpenGL is very slow)
    # -b = batch mode (exit when game closes)
    # -e = execute game
    exec ${pkgs.dolphin-emu}/bin/dolphin-emu --video_backend=Vulkan -b -e "$@"
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

  # PPSSPP (PSP) wrapper
  ppssppWrapper = pkgs.writeShellScriptBin "ppsspp-wrapper" ''
    # PPSSPP wrapper for Steam - standalone is much better than RetroArch core
    exec ${pkgs.ppsspp}/bin/ppsspp --fullscreen "$@"
  '';

  # melonDS (Nintendo DS) wrapper
  melondsWrapper = pkgs.writeShellScriptBin "melonds-wrapper" ''
    # melonDS wrapper for Steam - standalone with better features than RetroArch
    exec ${pkgs.melonDS}/bin/melonDS --fullscreen "$@"
  '';

  # Flycast (Dreamcast/Naomi/Atomiswave) wrapper
  flycastWrapper = pkgs.writeShellScriptBin "flycast-wrapper" ''
    # Flycast wrapper for Steam
    exec ${pkgs.flycast}/bin/flycast --config window:fullscreen=yes "$@"
  '';

  # Launcher script for Steam to invoke (switches to Kodi session)
  kodiLauncher = pkgs.writeShellScriptBin "kodi-launcher" ''
    echo "kodi" > /tmp/htpc-session-request
    # Shutdown Steam gracefully
    ${pkgs.steam}/bin/steam -shutdown
  '';

  # Script to switch from Kodi to Steam when a controller connects
  controllerSwitchScript = pkgs.writeShellScript "controller-switch-to-steam" ''
    SESSION_FILE="/tmp/htpc-session-request"
    KODI_API="http://localhost:8080/jsonrpc"

    # Check if Kodi is currently running by pinging JSON-RPC
    if ! ${pkgs.curl}/bin/curl -s -X POST \
         -H "Content-Type: application/json" \
         -d '{"jsonrpc":"2.0","method":"JSONRPC.Ping","id":1}' \
         "$KODI_API" 2>/dev/null | ${pkgs.gnugrep}/bin/grep -q '"result":"pong"'; then
      # Kodi not running (probably in Steam already) - do nothing
      exit 0
    fi

    # Check if we already requested a session switch (prevent double-trigger)
    if [ -f "$SESSION_FILE" ]; then
      exit 0
    fi

    # Request Steam session and quit Kodi
    echo "steam" > "$SESSION_FILE"

    # Send CEC wake to turn on TV (device is /dev/cec1 on this hardware)
    ${pkgs.v4l-utils}/bin/cec-ctl -d /dev/cec1 --image-view-on 2>/dev/null || true

    # Tell Kodi to quit (greetd will restart with Steam)
    ${pkgs.curl}/bin/curl -s -X POST \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","method":"Application.Quit","id":1}' \
      "$KODI_API" 2>/dev/null || true
  '';

in
{
  # udev rule: Trigger Steam switch when game controller connects
  # Matches joystick devices (js0, js1, etc.) which are created for game controllers
  services.udev.extraRules = ''
    # Game controller connected - switch to Steam if in Kodi
    ACTION=="add", SUBSYSTEM=="input", KERNEL=="js[0-9]*", TAG+="systemd", ENV{SYSTEMD_WANTS}="controller-switch-to-steam.service"
  '';

  # Systemd service triggered by udev when controller connects
  systemd.services.controller-switch-to-steam = {
    description = "Switch to Steam session when game controller connects";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${controllerSwitchScript}";
      # Run as htpc user to access Kodi API
      User = "htpc";
    };
  };

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
    ppsspp          # PSP
    melonDS         # Nintendo DS
    flycast         # Dreamcast / Naomi / Atomiswave

    # RetroArch (2D consoles)
    retroarchWithCores

    # Emulator wrappers for Steam launching
    dolphinWrapper
    pcsx2Wrapper
    duckstationWrapper
    retroarchWrapper
    ppssppWrapper
    melondsWrapper
    flycastWrapper

    # Session launcher for returning to Kodi from Steam
    kodiLauncher

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
