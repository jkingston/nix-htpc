{ kodiPackages, pkgs }:
kodiPackages.jellyfin.overrideAttrs (old: {
  pname = "kodi-jellyfin-htpc";
  version = "2.0.6";
  name = "kodi-jellyfin-htpc-2.0.6";
  __intentionallyOverridingVersion = true;

  patches = (old.patches or [ ]) ++ [
    ./player.patch
  ];

  # trickplay.py crops Jellyfin sprite sheets with Pillow. Keep that runtime
  # edge explicit instead of relying on Kodi's current transitive closure.
  propagatedBuildInputs = (old.propagatedBuildInputs or [ ]) ++ [
    kodiPackages.kodi.pythonPackages.pillow
  ];

  nativeCheckInputs = (old.nativeCheckInputs or [ ]) ++ [
    pkgs.buildPackages.python3Packages.pillow
  ];

  postPatch = (old.postPatch or "") + ''
    cp ${./trickplay.py} jellyfin_kodi/trickplay.py

    substituteInPlace release.yaml \
      --replace-fail "version: '2.0.0'" "version: '2.0.6'"

    substituteInPlace addon.xml \
      --replace-fail \
        '<import addon="script.module.websocket" version="1.6.4" />' \
        '<import addon="script.module.websocket" version="1.6.4" />
    <import addon="script.module.pil" version="5.1.0" />'

    test "$(grep -c 'TrickplayPreviewManager' jellyfin_kodi/player.py)" -eq 2
    test "$(grep -c 'trickplay_preview.stop()' jellyfin_kodi/player.py)" -eq 3
    test -f jellyfin_kodi/trickplay.py
  '';

  doCheck = true;
  checkPhase = ''
    runHook preCheck
    PYTHONDONTWRITEBYTECODE=1 \
      TRICKPLAY_MODULE="$PWD/jellyfin_kodi/trickplay.py" \
      HTPC_SETTINGS_ROOT=${../kodi-settings-addon} \
      python3 -B ${./test_trickplay.py}
    runHook postCheck
  '';

  postInstall = (old.postInstall or "") + ''
    grep -q 'version="2.0.6+py3"' \
      "$out/share/kodi/addons/$namespace/addon.xml"
    grep -q '<import addon="script.module.pil" version="5.1.0"' \
      "$out/share/kodi/addons/$namespace/addon.xml"
    test -f "$out/share/kodi/addons/$namespace/jellyfin_kodi/trickplay.py"
  '';
})
