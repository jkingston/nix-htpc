{ pkgs, ... }:
{
  programs.kodi = {
    enable = true;
    # Package with addons defined in kodi.nix for greetd

    # advancedsettings.xml - immutable settings
    settings = {
      services = {
        devicename = "htpc-server";
        webserver = "true";
        webserverport = "8080";
        esallinterfaces = "true";
        esenabled = "true";
        zeroconf = "true";
      };
    };

    # Per-addon settings
    addonSettings = {
      "plugin.video.jellyfin" = {
        server_address = "http://localhost:8096";
      };
    };

    # Media library sources
    sources = {
      video = [
        { name = "Media"; path = "/mnt/hdd/media"; }
      ];
    };
  };

  home.stateVersion = "25.11";
}
