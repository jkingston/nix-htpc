{ inputs, lib, pkgs, ... }:
{
  home-manager = {
    useGlobalPkgs = true;
    useUserPackages = true;
    backupFileExtension = "backup";
    users.htpc = {
      imports = [ ./htpc-home.nix ]
        ++ lib.optionals pkgs.stdenv.hostPlatform.isx86_64 [ ./gaming-home.nix ];
    };
  };
}
