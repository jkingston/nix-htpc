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

    # Note: Jellyfin addon settings are NOT managed by Home Manager.
    # The addon stores auth tokens, sync state, and SyncInstallRunDone flag
    # in settings.xml. Home Manager would overwrite these on every rebuild,
    # causing the setup wizard to re-appear. Configure via Kodi UI on first run.

    # Media library sources
    sources = {
      video = [
        { name = "Media"; path = "/mnt/hdd/media"; }
      ];
    };
  };

  home.stateVersion = "25.11";
}
