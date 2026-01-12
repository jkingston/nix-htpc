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
      # Prevent TV power-off when switching sessions (Kodi → Steam)
      cec = {
        poweroffonstandby = "false";
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

  # CEC peripheral adapter settings
  # Controls TV power behavior during session switching vs explicit shutdown
  #
  # Session switch (Kodi ↔ Steam): TV stays on
  #   - standby_devices=231 (None) - Don't send Standby on Kodi exit
  #   - send_inactive_source=0 - Don't announce "inactive" on exit
  #   - Session scripts send Image View On to keep TV awake
  #
  # TV turned off with remote: PC stays on
  #   - standby_pc_on_tv_standby=36044 (Ignore) - Don't suspend PC when TV sends standby
  #
  # Explicit power off (Kodi menu → Power Off): TV turns off
  #   - standby_tv_on_pc_standby=1 - Send Standby when system shuts down
  #
  # Enum values (from Kodi peripherals.xml):
  #   36037 = TV and AVR, 36038 = AVR only, 36039 = TV only, 231 = None
  #   36044 = Ignore, 36045 = Suspend, 36046 = Shutdown
  home.file.".kodi/userdata/peripheral_data/cec_CEC_Adapter.xml".text = ''
    <settings>
      <setting id="enabled" value="1"/>
      <setting id="activate_source" value="1"/>
      <setting id="standby_devices" value="231"/>
      <setting id="standby_devices_advanced" value=""/>
      <setting id="send_inactive_source" value="0"/>
      <setting id="standby_pc_on_tv_standby" value="36044"/>
      <setting id="standby_tv_on_pc_standby" value="1"/>
      <setting id="wake_devices" value="36037"/>
      <setting id="wake_devices_advanced" value=""/>
      <setting id="double_tap_timeout_ms" value="300"/>
      <setting id="button_repeat_rate_ms" value="0"/>
      <setting id="button_release_delay_ms" value="0"/>
      <setting id="pause_playback_on_deactivate" value="1"/>
    </settings>
  '';

  home.stateVersion = "25.11";
}
