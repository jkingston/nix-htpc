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
}
