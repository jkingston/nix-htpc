{
  description = "NixOS HTPC configuration for Raspberry Pi 4B";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    nixos-hardware.url = "github:NixOS/nixos-hardware";
    home-manager = {
      url = "github:nix-community/home-manager/release-25.11";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, nixos-hardware, home-manager, ... }@inputs: {
    nixosConfigurations = {
      htpc-pi = nixpkgs.lib.nixosSystem {
        system = "aarch64-linux";
        specialArgs = { inherit inputs; };
        modules = [
          "${nixpkgs}/nixos/modules/installer/sd-card/sd-image-aarch64.nix"
          nixos-hardware.nixosModules.raspberry-pi-4
          home-manager.nixosModules.home-manager
          ./hosts/htpc-pi
          ./modules/common.nix
          ./modules/home.nix
          ./modules/kodi.nix
          ./modules/cec.nix
        ];
      };
    };
  };
}
