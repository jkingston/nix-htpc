{ pkgs, ... }:
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
    sponsorblock
    youtube
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

  # Open firewall for Kodi remote apps
  networking.firewall.allowedTCPPorts = [ 8080 ];
  networking.firewall.allowedUDPPorts = [ 9777 ];

  # Disable console screen blanking.
  boot.kernelParams = [
    "consoleblank=0"
  ];
}
