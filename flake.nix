{
  description = "NixOS HTPC configuration for Raspberry Pi 4B";

  nixConfig = {
    extra-substituters = [
      "https://nixos-raspberrypi.cachix.org"
    ];
    extra-trusted-public-keys = [
      "nixos-raspberrypi.cachix.org-1:4iMO9LXa8BqhU+Rpg6LQKiGa2lsNh/j2oiYLNOQ5sPI="
    ];
  };

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    mesa-nixpkgs.url = "github:NixOS/nixpkgs/cc29bf866a5d2eddfbf83120b8b9d801e57548cb";
    rpi-kernel-nixpkgs.url = "github:NixOS/nixpkgs/4bcf8859f21b2a85a978abafdae1a3c29ad30562";
    nixos-raspberrypi.url = "github:nvmd/nixos-raspberrypi/develop";
    home-manager = {
      url = "github:nix-community/home-manager/release-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixos-raspberrypi, home-manager, ... }@inputs:
    let
      configurationRevision = self.rev or self.dirtyRev or "unknown-dirty";
      checkSystems = [
        "aarch64-darwin"
        "aarch64-linux"
      ];
      htpcPi = nixos-raspberrypi.lib.nixosSystem {
        specialArgs = { inherit inputs; };
        modules = [
          nixos-raspberrypi.nixosModules.sd-image
          nixos-raspberrypi.nixosModules.raspberry-pi-4.base
          nixos-raspberrypi.nixosModules.raspberry-pi-4.display-vc4
          home-manager.nixosModules.home-manager
          ./hosts/htpc-pi
          ./modules/common.nix
          ./modules/maintenance.nix
          ./modules/home.nix
          ./modules/kodi.nix
          ./modules/cec.nix
          {
            system.configurationRevision = configurationRevision;
          }
        ];
      };
    in
    {
      nixosConfigurations.htpc-pi = htpcPi;
      checks = inputs.nixpkgs.lib.genAttrs checkSystems (
        system:
        let
          pkgs = inputs.nixpkgs.legacyPackages.${system};
          qualityChecks = import ./checks {
            htpcConfiguration = htpcPi.config;
            inherit pkgs;
            lib = pkgs.lib;
            repositoryRoot = ./.;
          };
        in
        qualityChecks
        // inputs.nixpkgs.lib.optionalAttrs (system == "aarch64-linux") {
          htpc-pi = htpcPi.config.system.build.toplevel;
        }
      );
    };
}
