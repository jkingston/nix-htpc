{ pkgs, kodiPackages }:
kodiPackages.buildKodiAddon rec {
  pname = "bingie-htpc";
  namespace = "skin.bingie";
  version = "2.0.2.6";

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
  '';

  meta = {
    description = "Bingie with appliance-style HTPC playback and navigation";
    homepage = "https://github.com/matke-84/repository.bingie";
    license = pkgs.lib.licenses.gpl2Only;
  };
}
