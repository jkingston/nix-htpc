{ config, pkgs, lib, ... }:

{
  # Pier - HTPC game management tool
  # Minimal Nix layer - UV handles Python dependencies

  environment.systemPackages = [
    # UV for running the Python app
    pkgs.uv

    # Pier wrapper script
    (pkgs.writeShellScriptBin "pier" ''
      # Use shared venv location so non-root users can run pier
      export UV_PROJECT_ENVIRONMENT="/var/lib/pier/.venv"
      cd /etc/nixos/pier && exec ${pkgs.uv}/bin/uv run pier "$@"
    '')

    # steam-run is required for running AppImages and non-Nix binaries
    pkgs.steam-run
  ];

  # Create shared venv directory with appropriate permissions
  # 0775 allows users group to write (htpc is in users group)
  systemd.tmpfiles.rules = [
    "d /var/lib/pier 0775 root users -"
  ];

  # Auto-update timer (runs weekly)
  systemd.user.services.pier-auto-update = {
    description = "Auto-update game ports via pier";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "/run/current-system/sw/bin/pier update --auto";
    };
  };

  systemd.user.timers.pier-auto-update = {
    description = "Weekly pier update check";
    timerConfig = {
      OnCalendar = "weekly";
      Persistent = true;
      RandomizedDelaySec = "1h";
    };
    wantedBy = [ "timers.target" ];
  };
}
