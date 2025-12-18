{
  description = "NixOS HTPC configuration for Beelink SER5 Pro and Raspberry Pi 4B";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nixos-hardware.url = "github:NixOS/nixos-hardware";
    home-manager = {
      url = "github:nix-community/home-manager/release-25.11";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    declarative-jellyfin = {
      url = "github:Sveske-Juice/declarative-jellyfin";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, disko, nixos-hardware, home-manager, declarative-jellyfin, ... }@inputs: {
    nixosConfigurations = {
      htpc-server = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        specialArgs = { inherit inputs; };
        modules = [
          disko.nixosModules.disko
          home-manager.nixosModules.home-manager
          ./hosts/htpc-server
          ./modules/common.nix
          ./modules/home.nix
          ./modules/kodi.nix
          ./modules/cec.nix
          ./modules/jellyfin.nix
          ./modules/media-mount.nix
        ];
      };
      htpc-pi = nixpkgs.lib.nixosSystem {
        system = "aarch64-linux";
        specialArgs = { inherit inputs; };
        modules = [
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
