{ pkgs, kodiPackages }:
kodiPackages.buildKodiAddon rec {
  pname = "titan-bingie-mod-htpc";
  namespace = "skin.titan.bingie.mod";
  version = "2.2.2.1";

  src = pkgs.fetchFromGitHub {
    owner = "AchillesPunks";
    repo = "skin.titan.bingie.mod";
    rev = "1197a8e421b2836a1c18ea7223b5b6a1f4f2d7ff";
    hash = "sha256-xJzex4dml/xUnHFJFu64A+LCzIwiTGzV8Kun7nzSeMI=";
  };

  patches = [ ./htpc-playback.patch ];

  postPatch = ''
    # The higher version keeps an older/equal per-user copy from shadowing the
    # system add-on. Keep the add-on ID unchanged so Kodi retains skin settings.
    substituteInPlace addon.xml \
      --replace-fail 'version="2.2.2"' 'version="${version}"'

    # The nine play/resume controls close the information dialog immediately
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
    ' xml/IncludesDialogVideoInfo.xml \
      > xml/IncludesDialogVideoInfo.xml.tmp
    mv xml/IncludesDialogVideoInfo.xml.tmp xml/IncludesDialogVideoInfo.xml

    loadingHandlerCount="$(grep -c 'SetProperty(BingiePlaybackStarting' xml/IncludesDialogVideoInfo.xml)"
    if [ "$loadingHandlerCount" -ne 9 ]; then
      echo "Expected 9 BINGIE loading-shield handlers, found $loadingHandlerCount" >&2
      exit 1
    fi

    # Upstream deliberately closes the information dialog before starting
    # playback. Remove its extra one-second wait so the home screen is not
    # exposed while Kodi starts resolving the Jellyfin item.
    timerCount="$(grep -c 'AlarmClock(PlayMovie.*00:01,silent)' xml/IncludesDialogVideoInfo.xml)"
    if [ "$timerCount" -ne 32 ]; then
      echo "Expected 32 delayed BINGIE play handlers, found $timerCount" >&2
      exit 1
    fi

    sed -i \
      '/AlarmClock(PlayMovie/ s/,00:01,silent)/,00:00,silent)/g' \
      xml/IncludesDialogVideoInfo.xml

    if grep -q 'AlarmClock(PlayMovie.*00:01,silent)' xml/IncludesDialogVideoInfo.xml; then
      echo "A delayed BINGIE play handler remains after patching" >&2
      exit 1
    fi
  '';

  meta = {
    description = "BINGIE MOD with HTPC playback and chapter-navigation fixes";
    homepage = "https://github.com/AchillesPunks/skin.titan.bingie.mod";
    license = pkgs.lib.licenses.gpl2Only;
  };
}
