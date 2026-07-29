{ bingieMod, lib, osConfig, pkgs, ... }:
{
  home.file.".kodi/userdata/advancedsettings.xml".text = ''
    <advancedsettings>
      <services>
        <devicename>${osConfig.networking.hostName}</devicename>
        <webserver>false</webserver>
        <esallinterfaces>false</esallinterfaces>
        <esenabled>true</esenabled>
        <zeroconf>true</zeroconf>
      </services>
      <cec>
        <poweroffonstandby>false</poweroffonstandby>
      </cec>
    </advancedsettings>
  '';

  # One predictable playback contract for the CEC remote and keyboard. The
  # managed service owns Left/Right seek transactions so Kodi never alternates
  # its live playhead with a pending preview target.
  home.file.".kodi/userdata/keymaps/zz-htpc-remote.xml".text = ''
    <keymap>
      <FullscreenVideo>
        <remote>
          <up>NotifyAll(htpc.seek,osd-show)</up>
          <down>NotifyAll(htpc.seek,osd-show)</down>
          <left>NotifyAll(htpc.seek,left)</left>
          <right>NotifyAll(htpc.seek,right)</right>
          <select>NotifyAll(htpc.seek,primary)</select>
          <back>NotifyAll(htpc.seek,fullscreen-back)</back>
        </remote>
        <keyboard>
          <up>NotifyAll(htpc.seek,osd-show)</up>
          <down>NotifyAll(htpc.seek,osd-show)</down>
          <left>NotifyAll(htpc.seek,left)</left>
          <right>NotifyAll(htpc.seek,right)</right>
          <enter>NotifyAll(htpc.seek,primary)</enter>
          <backspace>NotifyAll(htpc.seek,fullscreen-back)</backspace>
          <escape>NotifyAll(htpc.seek,fullscreen-back)</escape>
        </keyboard>
      </FullscreenVideo>
      <VideoOSD>
        <remote>
          <up>NotifyAll(htpc.seek,osd-up)</up>
          <down>NotifyAll(htpc.seek,osd-down)</down>
          <left>NotifyAll(htpc.seek,osd-left)</left>
          <right>NotifyAll(htpc.seek,osd-right)</right>
          <select>NotifyAll(htpc.seek,osd-primary)</select>
          <back>NotifyAll(htpc.seek,osd-back)</back>
        </remote>
        <keyboard>
          <up>NotifyAll(htpc.seek,osd-up)</up>
          <down>NotifyAll(htpc.seek,osd-down)</down>
          <left>NotifyAll(htpc.seek,osd-left)</left>
          <right>NotifyAll(htpc.seek,osd-right)</right>
          <enter>NotifyAll(htpc.seek,osd-primary)</enter>
          <backspace>NotifyAll(htpc.seek,osd-back)</backspace>
          <escape>NotifyAll(htpc.seek,osd-back)</escape>
        </keyboard>
      </VideoOSD>
    </keymap>
  '';

  # Superseded by zz-htpc-remote.xml. This exact legacy file predates the
  # managed keymap and would otherwise leave duplicate fullscreen bindings.
  home.activation.removeLegacyKodiKeymap =
    lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -f \
        /home/htpc/.kodi/userdata/keymaps/cec-stop-playback.xml
    '';

  # Bingie uses Skin Shortcuts to generate XML inside its own add-on directory,
  # so it cannot run directly from the read-only Nix store. Keep the source and
  # patches reproducible in Nix, then install a writable managed copy in Kodi's
  # user add-on directory.
  home.activation.installBingie = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    $DRY_RUN_CMD ${pkgs.coreutils}/bin/mkdir -p /home/htpc/.kodi/addons/skin.bingie
    $DRY_RUN_CMD ${pkgs.rsync}/bin/rsync \
      -a --checksum --delete --chmod=Du+rwx,Fu+rw \
      ${bingieMod}/share/kodi/addons/skin.bingie/ \
      /home/htpc/.kodi/addons/skin.bingie/
  '';

  # Note: Jellyfin addon settings are NOT managed by Home Manager.
  # The addon stores auth tokens, sync state, and SyncInstallRunDone flag
  # in settings.xml. Home Manager would overwrite these on every rebuild,
  # causing the setup wizard to re-appear. Configure via Kodi UI on first run.

  # CEC peripheral adapter settings
  # Controls TV power behavior.
  #
  # Kodi boot: TV stays off and the current input is not changed
  #   - activate_source=0 - Don't announce Kodi as active during startup
  #   - wake_devices=231 (None) - Don't wake the TV or AVR
  #
  # The cec-tv-wake service arms after TV standby, then asks Kodi to become the
  # active source when the TV's CEC power status returns to on.
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
      <setting id="activate_source" value="0"/>
      <setting id="standby_devices" value="231"/>
      <setting id="standby_devices_advanced" value=""/>
      <setting id="send_inactive_source" value="0"/>
      <setting id="standby_pc_on_tv_standby" value="36044"/>
      <setting id="standby_tv_on_pc_standby" value="1"/>
      <setting id="wake_devices" value="231"/>
      <setting id="wake_devices_advanced" value=""/>
      <setting id="double_tap_timeout_ms" value="300"/>
      <setting id="button_repeat_rate_ms" value="0"/>
      <setting id="button_release_delay_ms" value="0"/>
      <setting id="pause_playback_on_deactivate" value="1"/>
    </settings>
  '';

  home.stateVersion = "25.11";
}
