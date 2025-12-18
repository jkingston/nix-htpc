{ config, pkgs, ... }:
{
  networking.hostName = "htpc-pi";

  # Enable native CEC support for Raspberry Pi in libcec
  nixpkgs.overlays = [
    (self: super: {
      libcec = super.libcec.override { withLibraspberrypi = true; };
    })
  ];

  # RPi4 specific settings (from nixos-hardware)
  hardware.raspberry-pi."4".apply-overlays-dtmerge.enable = true;
  hardware.deviceTree = {
    enable = true;
    filter = "*rpi-4-*.dtb";
  };

  # Boot settings for SD card
  boot.loader.grub.enable = false;
  boot.loader.generic-extlinux-compatible.enable = true;

  # Filesystem (from SD image, will be auto-resized on first boot)
  fileSystems."/" = {
    device = "/dev/disk/by-label/NIXOS_SD";
    fsType = "ext4";
  };

  # SSH for remote access
  services.openssh.enable = true;
  users.users.root.openssh.authorizedKeys.keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFOMJ1q1j4JRhT/VCzWGrHhFmCp/u2Lit5BaauaqR4hE"
  ];
}
