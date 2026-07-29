{ kodiPackages }:
kodiPackages.jellyfin.overrideAttrs (old: {
  pname = "kodi-jellyfin-htpc";
  version = "2.0.3";
  name = "kodi-jellyfin-htpc-2.0.3";
  __intentionallyOverridingVersion = true;

  patches = (old.patches or [ ]) ++ [
    ./player.patch
  ];

  postPatch = (old.postPatch or "") + ''
    cp ${./trickplay.py} jellyfin_kodi/trickplay.py

    substituteInPlace release.yaml \
      --replace-fail "version: '2.0.0'" "version: '2.0.3'"

    test "$(grep -c 'TrickplayPreviewManager' jellyfin_kodi/player.py)" -eq 2
    test "$(grep -c 'trickplay_preview.stop()' jellyfin_kodi/player.py)" -eq 3
    test -f jellyfin_kodi/trickplay.py
  '';

  doCheck = true;
  checkPhase = ''
    runHook preCheck
    PYTHONDONTWRITEBYTECODE=1 \
      python3 -B ${./test_trickplay.py}
    runHook postCheck
  '';

  postInstall = (old.postInstall or "") + ''
    grep -q 'version="2.0.3+py3"' \
      "$out/share/kodi/addons/$namespace/addon.xml"
    test -f "$out/share/kodi/addons/$namespace/jellyfin_kodi/trickplay.py"
  '';
})
