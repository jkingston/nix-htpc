{ pkgs, kodiPackages }:
kodiPackages.buildKodiAddon rec {
  pname = "bingie-htpc";
  namespace = "skin.bingie";
  version = "2.0.2.8";

  src = pkgs.fetchzip {
    url = "https://raw.githubusercontent.com/matke-84/repository.bingie/main/omega/skin.bingie/skin.bingie-2.0.2.zip";
    hash = "sha256-kK9EzmO/yEAp2LNh0Wf4hkPHHaX37F1JsJ3xU9Tn12g=";
  };

  patches = [
    ./htpc-playback.patch
    ./htpc-ux.patch
    ./htpc-seeking.patch
    ./htpc-trickplay.patch
  ];

  nativeBuildInputs = [ pkgs.libxml2 ];

  postPatch = ''
    # The higher version keeps an older/equal per-user copy from shadowing the
    # system add-on. Keep the add-on ID unchanged so Kodi retains skin settings.
    substituteInPlace addon.xml \
      --replace-fail 'version="2.0.2"' 'version="${version}"'

    # The ten play/resume controls close the information dialog immediately
    # before an AlarmClock(PlayMovie, ...) action. Raise a Home-window property
    # first so the patched loading shield covers Jellyfin stream resolution.
    awk '
      /Dialog\.Close\(movieinformation\)<\/onclick>/ {
        closeLine = $0
        if ((getline followingLine) > 0) {
          candidateLine = followingLine
          hasSecondLine = 0
          if (followingLine ~ /^[[:space:]]*<!--/) {
            hasSecondLine = (getline secondLine) > 0
            if (hasSecondLine)
              candidateLine = secondLine
          }
          if (candidateLine ~ /AlarmClock\(PlayMovie/) {
            indent = closeLine
            sub(/<onclick.*/, "", indent)
            print indent "<onclick>SetProperty(BingiePlaybackStarting,1,Home)</onclick>"
            print indent "<onclick>AlarmClock(BingiePlaybackTimeout,ClearProperty(BingiePlaybackStarting,Home),00:30,silent)</onclick>"
          }
          print closeLine
          print followingLine
          if (hasSecondLine)
            print secondLine
          next
        }
      }
      { print }
    ' 1080i/IncludesDialogVideoInfo.xml \
      > 1080i/IncludesDialogVideoInfo.xml.tmp
    mv 1080i/IncludesDialogVideoInfo.xml.tmp 1080i/IncludesDialogVideoInfo.xml

    loadingHandlerCount="$(grep -c 'SetProperty(BingiePlaybackStarting' 1080i/IncludesDialogVideoInfo.xml)"
    if [ "$loadingHandlerCount" -ne 10 ]; then
      echo "Expected 10 BINGIE loading-shield handlers, found $loadingHandlerCount" >&2
      exit 1
    fi

    # Upstream deliberately closes the information dialog before starting
    # playback. Remove its extra one-second wait so the home screen is not
    # exposed while Kodi starts resolving the Jellyfin item.
    timerCount="$(grep -c 'AlarmClock(PlayMovie.*00:01,silent)' 1080i/IncludesDialogVideoInfo.xml)"
    if [ "$timerCount" -ne 24 ]; then
      echo "Expected 24 delayed BINGIE play handlers, found $timerCount" >&2
      exit 1
    fi

    sed -i \
      '/AlarmClock(PlayMovie/ s/,00:01,silent)/,00:00,silent)/g' \
      1080i/IncludesDialogVideoInfo.xml

    if grep -q 'AlarmClock(PlayMovie.*00:01,silent)' 1080i/IncludesDialogVideoInfo.xml; then
      echo "A delayed BINGIE play handler remains after patching" >&2
      exit 1
    fi

    # Validate every well-formed XML file touched by this fork. A few unrelated
    # upstream includes are not strict XML, so keep this list intentionally
    # scoped instead of hiding real regressions with a blanket best-effort pass.
    xmllint --noout \
      addon.xml \
      1080i/Home.xml \
      1080i/IncludesBingie.xml \
      1080i/IncludesHomeBingie.xml \
      1080i/IncludesOSD.xml \
      1080i/IncludesViewsLayoutLandscape.xml \
      1080i/IncludesViewsLayoutPoster.xml \
      1080i/IncludesViewsLayoutSquare.xml \
      1080i/MyVideoNav.xml \
      1080i/VideoFullScreen.xml \
      1080i/VideoOSD.xml \
      1080i/VideoOSDBookmarks.xml \
      1080i/Custom_1158_AutoCloseOSD.xml

    test "$(grep -c 'NotifyAll(htpc.seek,timeline-left)' 1080i/IncludesOSD.xml)" -eq 1
    test "$(grep -c 'NotifyAll(htpc.seek,timeline-right)' 1080i/IncludesOSD.xml)" -eq 1
    test "$(grep -ci 'ActivateWindow(VideoBookmarks)' 1080i/IncludesOSD.xml)" -eq 2
    test "$(grep -c 'id="1901"' 1080i/IncludesOSD.xml)" -eq 1
    test "$(grep -c 'id="1902"' 1080i/IncludesOSD.xml)" -eq 1
    test "$(grep -c '<info>Window(Home).Property(htpc.seek.percent)</info>' 1080i/IncludesOSD.xml)" -eq 1
    test "$(grep -c 'Property(htpc.seek.previewbucket)' 1080i/IncludesOSD.xml)" -eq 20
    grep -q 'Window(Home).Property(htpc.service.ready).*StepForward' \
      1080i/IncludesOSD.xml
    grep -q 'htpc.service.ready.*htpc.seek.active.*htpc.service.ready.*htpc.chapter.open' \
      1080i/Custom_1158_AutoCloseOSD.xml
    test "$(grep -c '!String.IsEmpty(Window(Home).Property(htpc.service.ready)) + !String.IsEmpty(Window(Home).Property(htpc.seek.active)) + Player.HasVideo' 1080i/IncludesOSD.xml)" -eq 3
    test "$(grep -c 'HTPC_Movie_Genres_Row' 1080i/IncludesHomeBingie.xml)" -eq 2
    test "$(grep -c 'HTPC_TV_Genres_Row' 1080i/IncludesHomeBingie.xml)" -eq 2
  '';

  meta = {
    description = "Bingie with appliance-style HTPC playback and navigation";
    homepage = "https://github.com/matke-84/repository.bingie";
    license = pkgs.lib.licenses.gpl2Only;
  };
}
