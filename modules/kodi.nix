{ nixos-raspberrypi, pkgs, ... }:
let
  rpiPackages = nixos-raspberrypi.packages.aarch64-linux;
  kodiSettingsAddon = rpiPackages.kodi-gbm.packages.buildKodiAddon {
    pname = "htpc-settings";
    namespace = "service.htpc.settings";
    version = "1.1.0";
    src = ./kodi-settings-addon;
  };
  kodiWithAddons = rpiPackages.kodi-gbm.withPackages (kodiPkgs: with kodiPkgs; [
    jellyfin
    kodiSettingsAddon
  ]);
in
{
  # Auto-login to Kodi via greetd.
  services.greetd = {
    enable = true;
    settings = {
      default_session = {
        command = "${kodiWithAddons}/bin/kodi-standalone";
        user = "htpc";
      };
    };
  };

  # Kodi disables newly discovered third-party add-ons by default. Enable the
  # managed settings service once Kodi's local JSON-RPC endpoint is ready.
  systemd.services.kodi-settings = {
    description = "Enable managed Kodi settings";
    wantedBy = [ "multi-user.target" ];
    requires = [ "greetd.service" ];
    after = [ "greetd.service" ];
    partOf = [ "greetd.service" ];

    script = ''
      for ((attempt = 0; attempt < 60; attempt++)); do
        response="$(
          ${pkgs.coreutils}/bin/printf '%s\n' \
            '{"jsonrpc":"2.0","method":"Addons.SetAddonEnabled","params":{"addonid":"service.htpc.settings","enabled":true},"id":1}' \
            | ${pkgs.netcat-openbsd}/bin/nc -N -w 1 127.0.0.1 9090 \
            || true
        )"

        case "$response" in
          *'"result":"OK"'*) exit 0 ;;
        esac

        sleep 1
      done

      echo "Kodi did not enable service.htpc.settings within 60 seconds" >&2
      exit 1
    '';

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
  };

  # Keep the EventServer available for remote input and the local CEC helper.
  networking.firewall.allowedUDPPorts = [ 9777 ];

  # Disable console screen blanking.
  boot.kernelParams = [
    "consoleblank=0"
    # Full-frame 2160p HEVC exhausts smaller CMA pools and corrupts rpivid's
    # reference-frame queue. Direct kernel boot places the initrd low enough
    # for the Pi-recommended 512 MiB reservation to fit below the DMA limit.
    "cma=512M"
    # Keep the Kodi interface responsive at 1080p60. Kodi can still switch to
    # whitelisted UHD modes for playback through KMS.
    "video=HDMI-A-1:1920x1080@60"
  ];
}
