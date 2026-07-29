{ ... }:
{
  home-manager = {
    useGlobalPkgs = true;
    useUserPackages = true;
    backupFileExtension = "backup";
    users.htpc = {
      imports = [
        ./htpc-home.nix
      ];
    };
  };
}
