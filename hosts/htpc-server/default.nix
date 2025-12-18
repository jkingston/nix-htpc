{ config, pkgs, ... }:
{
  imports = [ ./disko.nix ];

  networking.hostName = "htpc-server";

  # AMD GPU - Mesa provides VAAPI support automatically
  hardware.graphics.enable = true;
  hardware.enableRedistributableFirmware = true;

  # Boot loader (UEFI)
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  # SSH for remote access
  services.openssh.enable = true;
  users.users.root.openssh.authorizedKeys.keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFOMJ1q1j4JRhT/VCzWGrHhFmCp/u2Lit5BaauaqR4hE"
  ];
}
