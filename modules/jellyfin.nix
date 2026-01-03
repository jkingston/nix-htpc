{ config, pkgs, inputs, ... }:
{
  imports = [ inputs.declarative-jellyfin.nixosModules.default ];

  services.jellyfin = {
    enable = true;
    openFirewall = true;
  };

  # Fix for declarative-jellyfin init script log file permissions
  systemd.tmpfiles.rules = [
    "f /var/log/jellyfin.txt 0644 jellyfin jellyfin -"
  ];

  services.declarative-jellyfin = {
    enable = true;

    system = {
      serverName = "htpc-server";
    };

    encoding = {
      enableHardwareEncoding = true;
      hardwareAccelerationType = "vaapi";
      hardwareDecodingCodecs = [ "h264" "hevc" "vp9" "av1" ];
    };

    users.admin = {
      mutable = true;
      password = "";  # Empty password - set via Jellyfin UI since mutable=true
      permissions.isAdministrator = true;
    };

    libraries = {
      movies = {
        contentType = "movies";
        pathInfos = [ "/mnt/hdd/media/movies" ];
      };
      tv = {
        contentType = "tvshows";
        pathInfos = [ "/mnt/hdd/media/tv" ];
      };
      music = {
        contentType = "music";
        pathInfos = [ "/mnt/hdd/media/music" ];
      };
    };
  };

  # Ensure Jellyfin can access GPU for VAAPI and media files
  users.users.jellyfin.extraGroups = [ "render" "video" "users" ];

  environment.systemPackages = with pkgs; [
    jellyfin-ffmpeg
  ];
}
