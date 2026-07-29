# HTPC BINGIE fork

`default.nix` packages the pinned Bingie 2.0.2 archive. Home Manager copies the
result into Kodi's user add-on directory with writable permissions because
Skin Shortcuts generates XML inside the skin directory at runtime. It keeps the
upstream add-on ID, `skin.bingie`, so the selected skin and settings survive.
The local version is higher than upstream at `2.0.2.6`.

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
parent-directory entries, disabling the mouse and debug overlay, and hiding
low-value maintenance actions in movie details. The Home Manager keymap makes
Up/Down reveal the controls without pausing, OK toggle Play/Pause and reveal
the controls, Left/Right signal the managed seek controller, and Back stop
playback after visible OSD layers have been dismissed.

`htpc-seeking.patch` routes the BINGIE timeline to the same controller and
keeps the OSD open while a target is pending. Discrete presses always move
exactly ten seconds and auto-commit as one absolute seek after 550ms of
inactivity. A measured held-button repeat signature enters gradually
accelerating scrub mode; releasing freezes the target until OK confirms or
Back cancels. The passive preview does not focus the timeline, so a new tap
after a short pause is still an auto-committing ten-second skip. Up/Down exits
that passive mode and exposes normal OSD navigation; explicitly focusing the
timeline provides an untimed, confirmable scrub path. From there Up opens the
chapter/bookmark picker when chapters exist.

`htpc-trickplay.patch` adds a smooth controller-driven target slider and a
clamped target card to BINGIE's primary timeline. The live playhead remains as
a small subdued marker while the bright target stays stable through decoder
settlement, so the cursor cannot flicker between the two positions. The target
time and delta work for every seekable video. When Jellyfin resolves an exact
matching frame, the card also shows that frame and chapter; media without
trickplay metadata does not display an empty image box.

The persistent controller lives in `modules/kodi-settings-addon`. It owns the
absolute target and commits exactly once with `Player.seekTime()`. It also
positions BINGIE controls at floating-point precision, avoiding the old
one-percent quantisation (72 seconds per step in a two-hour film). Jellyfin is
only an asynchronous frame supplier and tags each result with the target it
resolved, so a late cold-cache response cannot replace a newer preview.
Playback item identity and Kodi lifecycle notifications cancel stale
transactions during episode changes. Sliding quiet-period guards make held OK
or Back one semantic action instead of allowing repeats to traverse several
OSD layers, and per-event recovery keeps the service alive if a player call
races playback teardown.

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
