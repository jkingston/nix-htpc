{ config, pkgs, lib, ... }:
{
  # libcec for CEC support
  environment.systemPackages = with pkgs; [
    libcec
    v4l-utils
  ];

  # udev rules for CEC device access
  services.udev.extraRules = ''
    # Raspberry Pi vchiq (for native CEC)
    KERNEL=="vchiq", GROUP="video", MODE="0660"
    # CEC devices
    KERNEL=="cec[0-9]*", GROUP="video", MODE="0660"
    # Pulse-Eight USB adapter
    SUBSYSTEM=="tty", KERNEL=="ttyACM[0-9]*", ATTRS{idVendor}=="2548", GROUP="dialout", MODE="0660"
  '';

  # Ensure htpc user can access CEC devices
  users.users.htpc.extraGroups = [ "video" "dialout" ];
}
