# HTPC BINGIE fork

`default.nix` packages the pinned Bingie 2.0.2 archive as a Kodi system add-on.
It deliberately keeps the upstream add-on ID, `skin.bingie`, so an
existing installation keeps its selected skin and settings. The local add-on
version is one patch release higher than the pinned upstream version so Kodi
selects this system copy instead of an existing per-user `2.0.2` copy.

`htpc-playback.patch` contains the reviewable skin XML changes:

- a black loading shield and spinner over Home while Jellyfin resolves a video;
- always-visible chapter markers and native chapter selection from the timeline;
- stop VOD playback whenever fullscreen video is left while BINGIE's
  `ForceVideoPlaybackStop` setting is enabled.

`default.nix` adds the loading state to all ten information-dialog play controls
and changes all 24 normal play timers from one second to zero seconds. Both
counts are asserted during the build so an upstream layout change fails
visibly instead of silently leaving some handlers unpatched. Fullscreen video
clears the shield immediately; a 30-second timeout clears it if playback fails.

The package does not yet make BINGIE's third-party helper add-ons declarative.
The currently installed BINGIE dependencies remain in the Kodi user profile
and satisfy the system skin. A fresh profile needs those dependencies installed
before Kodi can enable BINGIE.

When updating the pin:

1. Change the archive URL, `version`, and expected upstream version in
   `default.nix`.
2. Update the source hash.
3. Rebase `htpc-playback.patch` and update the asserted play-handler count if
   upstream intentionally changes it.
4. Build the skin through the Pi configuration, then test playback from both
   the BINGIE information dialog and the Jellyfin library.
