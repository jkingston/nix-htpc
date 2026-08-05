{ pkgs, kodiPackages }:

let
  upstream = import ./upstream-assets.nix { inherit pkgs; };
  version = "2.0.2.13";

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
    for retired_path in \
      1080i/service-LibreELEC-Settings-mainWindow.xml \
      1080i/service-OpenELEC-Settings-mainWindow.xml \
      extras/openelec
    do
      test ! -e "$out/$retired_path"
    done
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
      1080i/Startup.xml \
      1080i/Custom_1101_StartUp.xml \
      1080i/Custom_1102_StartUp2.xml \
      1080i/Custom_1103_StartUpMask.xml \
      1080i/DialogBusy.xml \
      1080i/DialogButtonMenu.xml \
      1080i/DialogVideoInfo.xml \
      1080i/Home.xml \
      1080i/mainWindow.xml \
      1080i/script-skinshortcuts-bootstrap.xml \
      1080i/Includes.xml \
      1080i/IncludesAnimations.xml \
      1080i/IncludesVariables.xml \
      1080i/IncludesBingie.xml \
      1080i/IncludesDefaultSkinSettings.xml \
      1080i/IncludesFunctions.xml \
      1080i/IncludesHomeBingie.xml \
      1080i/IncludesHomeWidgets.xml \
      1080i/IncludesHTPCPlayback.xml \
      1080i/IncludesHTPCVideoOSD.xml \
      1080i/IncludesOSD.xml \
      1080i/IncludesSkinSettings.xml \
      1080i/IncludesViewsLayoutLandscape.xml \
      1080i/IncludesViewsLayoutPoster.xml \
      1080i/IncludesViewsLayoutSquare.xml \
      1080i/MyVideoNav.xml \
      1080i/DialogSeekBar.xml \
      1080i/VideoFullScreen.xml \
      1080i/VideoOSD.xml \
      1080i/VideoOSDBookmarks.xml \
      1080i/Custom_1158_AutoCloseOSD.xml \
      1080i/Custom_1192_HTPCVideoOSDReview.xml \
      extras/bingiesettings.xml

    test ! -e 1080i/script-skinshortcuts-includes.xml
    test -f shortcuts/htpc.properties.json
    ! grep -R -E -q \
      'jellyfintvshows|library://video/jellyfin|[0-9a-f]{32}' \
      shortcuts/mainmenu.DATA.xml \
      shortcuts/10000-1.DATA.xml \
      shortcuts/moviehub.DATA.xml \
      shortcuts/tvshowhub.DATA.xml \
      shortcuts/htpc.properties.json
    test "$(xmllint --xpath 'name(/*)' \
      1080i/script-skinshortcuts-bootstrap.xml)" = includes
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
    for removed_asset in \
      extras/media/bingie_intro_1080p.mp4 \
      extras/media/bingie_intro_2160p.mp4 \
      extras/media/bingie_splash.png
    do
      test ! -e "$removed_asset"
    done
    test -f extras/media/backgrounds/background.jpg
    test -f extras/media/snow/snow.png

    BINGIE_SKIN_ROOT="$PWD" \
      BINGIE_TOOLS_ROOT=${./tools} \
      BINGIE_UPSTREAM_ASSETS=${./upstream-assets.nix} \
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
      1080i/Startup.xml \
      1080i/Custom_1101_StartUp.xml \
      1080i/Custom_1102_StartUp2.xml \
      1080i/Custom_1103_StartUpMask.xml \
      1080i/Home.xml \
      1080i/mainWindow.xml \
      1080i/script-skinshortcuts-bootstrap.xml \
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
    test -f "$addon_dir/resources/htpc/osd/timeline-marker.svg"
    test -f "$addon_dir/resources/htpc/osd/timeline-marker.png"
    test -f "$addon_dir/resources/htpc/osd/chapter-up.svg"
    test -f "$addon_dir/resources/htpc/osd/chapter-up.png"
    test -f "$addon_dir/media/Textures.xbt"
    test -f "$addon_dir/resources/icon.png"
    test -f "$addon_dir/extras/media/backgrounds/background.jpg"
    test -f "$addon_dir/extras/media/snow/snow.png"
    for removed_asset in \
      bingie_intro_1080p.mp4 \
      bingie_intro_2160p.mp4 \
      bingie_splash.png
    do
      test ! -e "$addon_dir/extras/media/$removed_asset"
    done
    test ! -e "$addon_dir/1080i/script-skinshortcuts-includes.xml"
    test -f "$addon_dir/shortcuts/htpc.properties.json"
    for retired_path in \
      1080i/service-LibreELEC-Settings-mainWindow.xml \
      1080i/service-OpenELEC-Settings-mainWindow.xml \
      extras/openelec
    do
      test ! -e "$addon_dir/$retired_path"
    done
  '';

  meta = {
    description = "BINGIE fork with appliance-style HTPC playback and navigation";
    homepage = "https://github.com/matke-84/repository.bingie";
    license = pkgs.lib.licenses.gpl2Only;
  };
}
