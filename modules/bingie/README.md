# HTPC BINGIE fork

`default.nix` packages the pinned BINGIE 2.0.2 archive as `skin.bingie`
version 2.0.2.7. The NixOS greetd pre-start stages and validates the immutable
package, then checksum-synchronises it into Kodi's writable user add-on
directory while Kodi is stopped. It preserves only Skin Shortcuts' generated
`1080i/script-skinshortcuts-includes.xml`; managed skin sources remain
declarative.

`htpc-playback.patch` contains playback-start and exit fixes:

- a black loading shield covers Home while Jellyfin resolves a selected video;
- all information-dialog play timers start immediately instead of exposing
  Home for one second;
- Play, or Resume for a partially watched movie, receives initial focus in
  movie details;
- chapter markers are always visible when the player reports chapters;
- leaving fullscreen VOD stops playback when BINGIE's
  `ForceVideoPlaybackStop` setting is enabled.

`htpc-ux.patch` is the Home and library polish pass:

- spotlight opens on Play, Right selects More Info, and Left from Play opens
  the sidebar without the old double-action focus bug;
- Right closes the sidebar instead of opening Movies/TV submenu blades;
- ordinary movie, episode, and TV rows open details;
- separate Movie Genres and TV Genres rows appear only in their matching hub
  and only when populated;
- resume items use a thin edge-to-edge progress bar and the bottom-right
  BINGIE logo is hidden;
- single-profile, maintenance-heavy, and desktop-style actions are omitted.

The managed settings service disables parent-directory entries, the mouse,
debug overlays, OSD auto-pause, and low-value video-information actions.
Fullscreen Up/Down reveal the OSD without pausing. OK toggles play/pause and
reveals it. Left/Right enter the managed seek path. Back unwinds a pending
interaction, then the OSD, then stops playback.

`htpc-seeking.patch` connects BINGIE's focused timeline to the same controller
and keeps the OSD open while a seek transaction is active:

- a Left/Right tap, whether the OSD was hidden or its timeline is focused,
  previews an exact ten-second skip and commits one coalesced absolute seek
  after 550 ms of inactivity;
- rapid distinct taps remain exact ten-second steps;
- the measured CEC repeat signature of a held button enters pause-owned,
  gradually accelerating scrubbing without rolling the cursor backwards while
  the hold is classified;
- after a released hold, the next press starts again as an exact ten-second
  step and must prove a fresh hold before acceleration resumes;
- only a proven hold pauses playback and enters a pending scrub; OK commits it
  and Back cancels it;
- focus movement is native when no modal seek is pending, with safe Kodi
  fallbacks if the service readiness lease expires.

`htpc-trickplay.patch` adds one cursor-following exact preview, a rounded target
time/delta, a stable target marker, and a subdued actual playhead. The live
playhead is never reused as the target, so decoder settlement cannot flicker
the cursor back to the current position. Up from the focused timeline exposes
a custom chapter-only thumbnail rail after Jellyfin has atomically published
every retained chapter frame. Up exits that rail to the top controls;
Down/Back return to the timeline. Kodi's native bookmark window remains a
separate OSD facility.

The persistent controller in `modules/kodi-settings-addon` owns absolute
targets, pause/resume ownership, commit/cancel state, input routing, focus, and
presentation. It rejects stale player callbacks and media changes. The
readiness property is a two-second renewable lease. Custom presentation and
OSD-timeout suppression are gated on that lease so stale state cannot leave the
interface stuck if the service exits.

`modules/jellyfin/trickplay.py` is only the asynchronous media supplier. Exact
previews use a latest-target-wins foreground lane, complete playback/seek/frame
tokens, and double-buffered files. Neighbor and chapter prefetching share one
bounded background lane with in-flight sprite deduplication and bounded
caches/retries. Late or partially published results cannot replace the current
preview. The chapter contract is explicit, sanitized, capped at 24, and becomes
ready as one stable manifest only after every retained frame exists. If the
server's chapter-image endpoint fails, the producer materializes the exact
chapter-position trick-play frame instead.

The package does not yet declare BINGIE's third-party helper add-ons. The
already-installed Pi profile retains those dependencies. A fresh profile must
install them before Kodi can enable BINGIE.

When updating the pin:

1. Change the archive URL, `version`, and expected upstream version in
   `default.nix`.
2. Update the source hash.
3. Rebase all four `htpc-*.patch` files and update intentional build-time
   assertions.
4. Run both Python test suites and the Pi configuration build.
5. Test Home details, spotlight/sidebar navigation, hidden taps, held scrubs,
   OK/Back, chapter browsing, playback exit, and a cold-cache trick-play seek
   on the Pi.
