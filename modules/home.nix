{ nixos-raspberrypi, pkgs, ... }:
let
  rpiPackages = nixos-raspberrypi.packages.aarch64-linux;
  bingieMod = import ./bingie {
    inherit pkgs;
    kodiPackages = rpiPackages.kodi-gbm.packages;
  };
in
{
  home-manager = {
    useGlobalPkgs = true;
    useUserPackages = true;
    backupFileExtension = "backup";
    extraSpecialArgs = {
      inherit bingieMod;
    };
    users.htpc = {
      imports = [
        ./htpc-home.nix
      ];
    };
  };
}
