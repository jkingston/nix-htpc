{ config, pkgs, lib, ... }:
let
  # Fix script.module.pil by populating its lib/ directory with actual Pillow
  kodiFixed = pkgs.kodi-gbm.overrideAttrs (old: {
    postInstall = (old.postInstall or "") + ''
      PIL_DIR="$out/share/kodi/addons/script.module.pil/lib"
      mkdir -p "$PIL_DIR"
      cp -r ${pkgs.python3Packages.pillow}/${pkgs.python3.sitePackages}/PIL "$PIL_DIR/"
      cp -r ${pkgs.python3Packages.pillow}/${pkgs.python3.sitePackages}/Pillow*.dist-info "$PIL_DIR/" 2>/dev/null || true
    '';
  });

  kodiWithAddons = kodiFixed.withPackages (kodiPkgs: with kodiPkgs; [
    jellyfin
    inputstream-adaptive
  ]);
in
{
  # Auto-login to Kodi session via greetd
  services.greetd = {
    enable = true;
    settings = {
      default_session = {
        command = "${kodiWithAddons}/bin/kodi-standalone";
        user = "htpc";
      };
    };
  };

  # Open firewall for Kodi remote apps
  networking.firewall.allowedTCPPorts = [ 8080 ];
  networking.firewall.allowedUDPPorts = [ 9777 ];

  # Disable screen blanking and audio power saving
  boot.kernelParams = [
    "consoleblank=0"
    "snd_hda_intel.power_save=0"
    "snd_hda_intel.power_save_controller=N"
  ];

  # AMD GBM vblank workaround (GitHub xbmc/xbmc#26167)
  # Kodi GBM on AMD APUs drops ~5fps due to MALL idle optimization bug.
  # This service monitors FPS via Kodi API and enables DRM debug when needed.
  systemd.services.amd-fps-fix-monitor = {
    description = "AMD FPS bug monitor and fix";
    wantedBy = [ "graphical.target" ];
    after = [ "greetd.service" ];
    path = with pkgs; [ curl jq bc gnugrep ];
    serviceConfig = {
      Type = "simple";
      Restart = "always";
      RestartSec = 10;
      ExecStart = pkgs.writeShellScript "amd-fps-fix-monitor" ''
        KODI_API="http://localhost:8080/jsonrpc"
        DEBUG_FILE="/sys/module/drm/parameters/debug"
        MAX_DEBUG_SECONDS=300
        COOLDOWN_SECONDS=60
        FPS_THRESHOLD=0.95

        declare -a fps_samples=()
        SAMPLE_SIZE=5

        get_fps() {
          curl -s -X POST -H "Content-Type: application/json" \
            -d '{"jsonrpc":"2.0","method":"XBMC.GetInfoLabels","params":{"labels":["System.FPS","System.ScreenMode"]},"id":1}' \
            "$KODI_API" 2>/dev/null
        }

        parse_fps() { echo "$1" | jq -r '.result["System.FPS"]' 2>/dev/null; }
        parse_target() { echo "$1" | jq -r '.result["System.ScreenMode"]' 2>/dev/null | grep -oP '@ \K[0-9.]+'; }

        average() {
          local sum=0
          for v in "''${fps_samples[@]}"; do sum=$(echo "$sum + $v" | bc -l); done
          echo "scale=2; $sum / ''${#fps_samples[@]}" | bc -l
        }

        echo "Waiting for Kodi API..."
        until curl -s "$KODI_API" >/dev/null 2>&1; do sleep 5; done
        echo "Kodi API ready"

        last_target=""
        while true; do
          metrics=$(get_fps)
          fps=$(parse_fps "$metrics")
          target=$(parse_target "$metrics")

          # Log refresh rate changes
          if [[ -n "$target" && "$target" != "null" && "$target" != "$last_target" ]]; then
            echo "Refresh rate changed: $last_target -> $target Hz"
            last_target="$target"
            fps_samples=()  # Reset samples on mode change
          fi

          if [[ -z "$fps" || -z "$target" || "$target" == "null" ]]; then
            sleep 5; continue
          fi

          fps_samples+=("$fps")
          if (( ''${#fps_samples[@]} > SAMPLE_SIZE )); then
            fps_samples=("''${fps_samples[@]:1}")
          fi

          if (( ''${#fps_samples[@]} < SAMPLE_SIZE )); then
            sleep 2; continue
          fi

          avg_fps=$(average)
          ratio=$(echo "$avg_fps / $target" | bc -l)

          if (( $(echo "$ratio < $FPS_THRESHOLD" | bc -l) )); then
            echo "MALL bug detected: avg=$avg_fps target=$target"
            echo 0xf > "$DEBUG_FILE"

            start=$SECONDS
            while (( SECONDS - start < MAX_DEBUG_SECONDS )); do
              sleep 2
              fps=$(parse_fps "$(get_fps)")
              [[ -n "$fps" ]] && ratio=$(echo "$fps / $target" | bc -l)
              if (( $(echo "$ratio >= 0.98" | bc -l) )); then
                echo "Fixed: fps=$fps"
                break
              fi
            done

            echo 0 > "$DEBUG_FILE"
            fps_samples=()
            sleep $COOLDOWN_SECONDS
          fi
          sleep 2
        done
      '';
    };
  };
}
