{ config, pkgs, ... }:
{
  # Enable podman for OCI containers
  virtualisation.podman.enable = true;

  # Run Jellyfin as a container
  virtualisation.oci-containers = {
    backend = "podman";
    containers.jellyfin = {
      image = "jellyfin/jellyfin:latest";
      autoStart = true;
      ports = [ "8096:8096" ];
      volumes = [
        "/var/lib/jellyfin:/config"
        "/var/cache/jellyfin:/cache"
        "/mnt/hdd/media:/media:ro"
      ];
      extraOptions = [
        "--device=/dev/dri:/dev/dri"  # VAAPI hardware acceleration
      ];
    };
  };


  # Open firewall for Jellyfin web UI
  networking.firewall.allowedTCPPorts = [ 8096 ];
}
