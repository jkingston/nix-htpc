{
  # The pinned Raspberry Pi kernel builds bcm2835_wdt in. Have systemd PID 1
  # feed it; if the kernel or PID 1 stops responding for a full minute, the
  # watchdog resets the appliance.
  systemd.settings.Manager.RuntimeWatchdogSec = "60s";

  # Keep recent generations for rollback while bounding SD-card usage. Do not
  # catch up a missed collection immediately after boot, when Kodi is in use.
  nix.gc = {
    automatic = true;
    dates = "Sat 05:30";
    persistent = false;
    options = "--delete-older-than 30d";
  };

  # Store optimisation scans the whole Nix store and adds avoidable SD-card
  # I/O. Leave both scheduled and build-time optimisation disabled.
  nix.optimise.automatic = false;
  nix.settings.auto-optimise-store = false;
}
