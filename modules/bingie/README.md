# HTPC BINGIE fork

`default.nix` packages the pinned Bingie 2.0.2 archive. Home Manager copies the
result into Kodi's user add-on directory with writable permissions because
Skin Shortcuts generates XML inside the skin directory at runtime. It keeps the
upstream add-on ID, `skin.bingie`, so the selected skin and settings survive.
The local version is higher than upstream at `2.0.2.4`.

`htpc-playback.patch` contains the playback-start fixes:

- a black loading shield and spinner over Home while Jellyfin resolves a video;
- always-visible chapter markers and native chapter selection from the timeline;
- Play (or Resume for a partially watched movie) receives focus when movie
  details open;
- stop VOD playback whenever fullscreen video is left while BINGIE's
  `ForceVideoPlaybackStop` setting is enabled.

`htpc-ux.patch` is the TV-first interaction and presentation pass:

- playback controls no longer pause merely because the OSD opened, animate
  faster, and hide VOD controls already covered by the remote;
- choosing the focused timeline opens Kodi's chapter/bookmark picker as a
  secondary navigation path;
- Home spotlight selection always opens details, with the information action
  visibly selected and no extra Right/Left mode-switch press;
- resume items use a thin edge-to-edge progress bar and the bottom-right
  BINGIE branding is hidden;
- the single-profile avatar, maintenance-heavy library blade actions, and
  desktop-style power actions are omitted.

The managed Kodi settings service complements the skin patch by removing
parent-directory entries, applying seek steps immediately, disabling the mouse,
and hiding low-value maintenance actions in movie details. The Home Manager
keymap makes Up/Down/OK open the playback controls, Left/Right seek, and Back
stop playback.

`htpc-seeking.patch` makes remote seeking predictable: the first Left/Right
press always enters a pending seek, repeated presses accumulate against that
target, and the short OSD transitions stay visible while Kodi is seeking.

`htpc-trickplay.patch` displays the pending seek position on BINGIE's primary
timeline. When the managed Jellyfin add-on supplies a preview, the same overlay
shows the actual Jellyfin trickplay frame and current chapter above the
timeline. Non-Jellyfin playback retains the native time bubble and seek
indicator.

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
3. Rebase all four `htpc-*.patch` files, and update the asserted play-handler
   count if upstream intentionally changes it.
4. Build the skin through the Pi configuration, then test playback from both
   the BINGIE information dialog and the Jellyfin library.
