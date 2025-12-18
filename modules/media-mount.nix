{ config, lib, ... }:
{
  # Mount external USB drive by label (exFAT, labeled "hdd")
  fileSystems."/mnt/hdd" = {
    device = "/dev/disk/by-label/hdd";
    fsType = "exfat";
    options = [ "nofail" "x-systemd.automount" "x-systemd.device-timeout=5s" "x-systemd.mount-timeout=5s" ];
  };
}
