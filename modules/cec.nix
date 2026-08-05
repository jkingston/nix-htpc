{
  lib,
  pkgs,
  ...
}:
let
  kodiPassiveEvidence = import ./kodi-passive-evidence/package.nix {
    inherit lib pkgs;
  };
in
{
  imports = [ ./kodi-cec-policy.nix ];

  # libcec for CEC support
  environment.systemPackages = with pkgs; [
    kodiPassiveEvidence
    libcec
    v4l-utils
  ];

  # Linux restricts CEC monitor mode to CAP_NET_ADMIN. The fixed, no-argument
  # evidence producer runs through the existing root SSH boundary instead of
  # adding sudo rules, file capabilities, a privileged daemon, or a setuid
  # executable.
  system.build.kodiPassiveEvidence = kodiPassiveEvidence;

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
