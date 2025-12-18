{ config, pkgs, ... }:
{
  time.timeZone = "Europe/London";
  i18n.defaultLocale = "en_GB.UTF-8";

  # HTPC user
  users.users.htpc = {
    isNormalUser = true;
    extraGroups = [ "video" "audio" "render" "input" ];
  };

  # Zram swap (compressed RAM - quieter than disk swap, ideal for HTPC)
  zramSwap = {
    enable = true;
    algorithm = "zstd";
    memoryPercent = 50;
  };

  # Audio via PipeWire
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    pulse.enable = true;
  };

  # Avahi/mDNS for .local hostname resolution (Bonjour)
  services.avahi = {
    enable = true;
    nssmdns4 = true;  # Enable .local resolution for IPv4
    publish = {
      enable = true;
      addresses = true;     # Publish hostname.local
      workstation = true;   # Announce as workstation
    };
  };

  # Enable flakes for future updates
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  networking.networkmanager.enable = true;

  system.stateVersion = "25.11";
}
