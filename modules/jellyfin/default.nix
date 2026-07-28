{ kodiPackages }:
kodiPackages.jellyfin.overrideAttrs (old: {
  pname = "kodi-jellyfin-htpc";

  patches = (old.patches or [ ]) ++ [
    ./player.patch
  ];

  postPatch = (old.postPatch or "") + ''
    cp ${./trickplay.py} jellyfin_kodi/trickplay.py

    substituteInPlace addon.xml \
      --replace-fail 'version="2.0.0+py3"' 'version="2.0.0.1+py3"'

    test "$(grep -c 'TrickplayPreviewManager' jellyfin_kodi/player.py)" -eq 2
    test "$(grep -c 'trickplay_preview.stop()' jellyfin_kodi/player.py)" -eq 3
    test -f jellyfin_kodi/trickplay.py
  '';
})
