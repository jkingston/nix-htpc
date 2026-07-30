{ pkgs, kodiPackages }:

let
  upstream = import ./upstream-assets.nix { inherit pkgs; };
  version = "2.0.2.9";

  # Keep reviewable skin source in this repository. Only the large immutable
  # payloads listed in upstream-assets.nix come from the hash-pinned archive.
  assembledSource = pkgs.runCommand "bingie-htpc-source-${version}" { } ''
    mkdir -p "$out"
    cp -R ${./src}/. "$out/"
    chmod -R u+w "$out"
    rm "$out/.gitattributes"

    ${pkgs.lib.concatMapStringsSep "\n" (path: ''
      if [ -d "${upstream.source}/${path}" ]; then
        mkdir -p "$out/${path}"
        cp -R "${upstream.source}/${path}/." "$out/${path}/"
      else
        mkdir -p "$out/${builtins.dirOf path}"
        cp "${upstream.source}/${path}" "$out/${path}"
      fi
    '') upstream.binaryPaths}

    test ! -e "$out/1080i/script-skinshortcuts-includes.xml"
  '';
in
kodiPackages.buildKodiAddon {
  pname = "bingie-htpc";
  namespace = "skin.bingie";
  inherit version;

  src = assembledSource;

  nativeBuildInputs = [
    pkgs.buildPackages.libxml2
    pkgs.buildPackages.python3
  ];

  doCheck = true;
  checkPhase = ''
    runHook preCheck

    xmllint --noout \
      addon.xml \
      1080i/Home.xml \
      1080i/Includes.xml \
      1080i/IncludesBingie.xml \
      1080i/IncludesHomeBingie.xml \
      1080i/IncludesHTPCPlayback.xml \
      1080i/IncludesHTPCVideoOSD.xml \
      1080i/IncludesOSD.xml \
      1080i/IncludesViewsLayoutLandscape.xml \
      1080i/IncludesViewsLayoutPoster.xml \
      1080i/IncludesViewsLayoutSquare.xml \
      1080i/MyVideoNav.xml \
      1080i/DialogSeekBar.xml \
      1080i/VideoFullScreen.xml \
      1080i/VideoOSD.xml \
      1080i/VideoOSDBookmarks.xml \
      1080i/Custom_1158_AutoCloseOSD.xml \
      1080i/Custom_1192_HTPCVideoOSDReview.xml

    test ! -e 1080i/script-skinshortcuts-includes.xml
    test "$(grep -c 'SetProperty(BingiePlaybackStarting' \
      1080i/IncludesDialogVideoInfo.xml)" -eq 10
    test "$(grep -c 'AlarmClock(PlayMovie.*00:00,silent)' \
      1080i/IncludesDialogVideoInfo.xml)" -eq 24
    ! grep -q 'AlarmClock(PlayMovie.*00:01,silent)' \
      1080i/IncludesDialogVideoInfo.xml
    test "$(grep -c 'HTPC_Movie_Genres_Row' \
      1080i/IncludesHomeBingie.xml)" -eq 2
    test "$(grep -c 'HTPC_TV_Genres_Row' \
      1080i/IncludesHomeBingie.xml)" -eq 2

    BINGIE_SKIN_ROOT="$PWD" \
      BINGIE_TOOLS_ROOT=${./tools} \
      HTPC_SETTINGS_ROOT=${../kodi-settings-addon} \
      HTPC_HOME_MODULE=${../htpc-home.nix} \
      PYTHONDONTWRITEBYTECODE=1 \
      python3 -B -m unittest discover -s ${./tests} -p 'test_*.py'
    python3 ${./tools}/generate_preview_anchors.py \
      --check 1080i/IncludesHTPCPlayback.xml

    runHook postCheck
  '';

  postInstall = ''
    addon_dir="$out/share/kodi/addons/skin.bingie"
    for required_file in \
      addon.xml \
      1080i/Home.xml \
      1080i/DialogSeekBar.xml \
      1080i/Includes.xml \
      1080i/IncludesHTPCPlayback.xml \
      1080i/IncludesHTPCVideoOSD.xml \
      1080i/IncludesOSD.xml \
      1080i/VideoOSD.xml \
      1080i/Custom_1158_AutoCloseOSD.xml \
      1080i/Custom_1192_HTPCVideoOSDReview.xml
    do
      test -f "$addon_dir/$required_file"
    done

    test -f "$addon_dir/resources/review/seek-25.png"
    test -f "$addon_dir/resources/review/seek-75.png"
    test -f "$addon_dir/media/Textures.xbt"
    test -f "$addon_dir/resources/icon.png"
    test ! -e "$addon_dir/1080i/script-skinshortcuts-includes.xml"
  '';

  meta = {
    description = "BINGIE fork with appliance-style HTPC playback and navigation";
    homepage = "https://github.com/matke-84/repository.bingie";
    license = pkgs.lib.licenses.gpl2Only;
  };
}
