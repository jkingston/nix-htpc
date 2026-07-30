{ lib, osConfig, pkgs, ... }:
let
  cecPeripheralData = osConfig.htpc.cec.capturePolicy.peripheralData;
in
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

  # VideoOSD directions are delivered directly to the focused skin control.
  # During fullscreen playback the managed service distinguishes exact
  # ten-second taps from the CEC adapter's repeat cadence for a held button,
  # then owns the resulting pause/scrub transaction. Up/Down stay native and
  # never pause merely to reveal the OSD.
  home.file.".kodi/userdata/keymaps/zz-htpc-remote.xml".text = ''
    <keymap>
      <FullscreenVideo>
        <remote>
          <up>ActivateWindow(VideoOSD)</up>
          <down>ActivateWindow(VideoOSD)</down>
          <left>NotifyAll(htpc.seek,left)</left>
          <right>NotifyAll(htpc.seek,right)</right>
          <select>NotifyAll(htpc.seek,primary)</select>
          <back>NotifyAll(htpc.seek,fullscreen-back)</back>
        </remote>
        <keyboard>
          <up>ActivateWindow(VideoOSD)</up>
          <down>ActivateWindow(VideoOSD)</down>
          <left>NotifyAll(htpc.seek,left)</left>
          <right>NotifyAll(htpc.seek,right)</right>
          <enter>NotifyAll(htpc.seek,primary)</enter>
          <backspace>NotifyAll(htpc.seek,fullscreen-back)</backspace>
          <escape>NotifyAll(htpc.seek,fullscreen-back)</escape>
        </keyboard>
      </FullscreenVideo>
      <VideoOSD>
        <remote>
          <select>NotifyAll(htpc.seek,osd-primary)</select>
          <back>NotifyAll(htpc.seek,osd-back)</back>
        </remote>
        <keyboard>
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

  # Note: Jellyfin addon settings are NOT managed by Home Manager.
  # The addon stores auth tokens, sync state, and SyncInstallRunDone flag
  # in settings.xml. Home Manager would overwrite these on every rebuild,
  # causing the setup wizard to re-appear. Configure via Kodi UI on first run.

  # The typed CEC policy owns this path, setting order, and exact XML.
  home.file.${cecPeripheralData.homeRelativePath}.source =
    cecPeripheralData.source;

  home.stateVersion = "25.11";
}
