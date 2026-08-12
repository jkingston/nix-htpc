# nix-htpc BINGIE fork changes

This fork starts from BINGIE 2.0.2. The initial local-source import on
2026-07-30 materializes the previously deployed Nix patches and build-time
transformations as add-on version `2.0.2.8`.

## Playback and information screens

- Focus Play or Resume when a movie information screen opens.
- Remove the extra one-second delay before starting media.
- Cover the home screen with a loading shield while Kodi resolves playback.
- Stop non-live video when leaving fullscreen playback.
- Show chapter markers whenever the video actually has chapters.

## Home and library navigation

- Prevent spotlight Left navigation from opening the side menu while moving
  from More Info to Play.
- Bypass the Movies and TV Shows side-menu submenus.
- Add library-backed movie and TV genre rows.
- Remove the bottom-right BINGIE logo.
- Widen and simplify home-card watched-progress presentation.
- Remove desktop-oriented context actions and make remote focus/navigation
  deterministic across the modified library layouts.

## Playback controls and seeking

- Use the managed HTPC seek controller for timeline Left, Right, Up, OK, focus,
  and blur events while preserving native fallbacks.
- Keep the OSD open during active seek and chapter interactions.
- Shorten OSD transition timings.
- Add the chapter affordance, target/actual seek markers, cursor-following
  trick-play preview, rounded seek time, and confirm/cancel hint.

The behavioral controller and Kodi settings integration live outside the skin
source. See the repository history for subsequent changes and the rationale
behind each implementation revision.

## Version 2.0.2.9

- Replace the property-driven target slider with Kodi ranges controls, whose
  label/CSV path supports finite target positions from window properties.
- Render one authoritative target fill and marker while a seek is active;
  hide the native actual-position fill and marker until decoder progress has
  converged.
- Publish two complete presentation slots and flip their selector only after
  fill, marker, time, prompt, and preview belong to the same revision.
- Move the trick-play card in deterministic one-percent timeline anchors and
  retain its validated frame through the decoder-position handoff.
- Keep a media-bound logical seek target authoritative across missing/late
  Kodi callbacks, pause-owned resume, and successive gestures until the raw
  decoder clock is stable at that target.
- Preserve attribution for an already-issued fixed skip when Back abandons a
  newer, still-optimistic gesture.
- Keep the OSD open through visual settlement, while allowing Back to dismiss
  it after an already-issued short seek without misreporting cancellation.

## Version 2.0.2.11

Version `2.0.2.10` was a rejected deployment trial and is intentionally not
reused.

- Make the startup lifecycle media-free while preserving the first-run flow
  and a self-clearing compatibility mask.
- Remove the obsolete splash controls, settings, defaults, translations, and
  trailer timing branches.
- Exclude the two upstream intro videos and splash image from the assembled
  add-on while retaining independently used backgrounds and snow artwork.

## Version 2.0.2.12

- Remove the inherited OpenELEC and LibreELEC settings windows and their
  exclusive image payload. Those platform add-ons are outside this NixOS
  appliance's runtime closure.

## Version 2.0.2.13

- Enlarge the timeline playhead texture and map it through a texture-padded
  ranges control, keeping one round playhead continuous from 0% through 100%.
- Replace the font-dependent chapter arrow with a symmetric, centered skin
  asset.
- Extend the headless OSD review fixtures to cover exact 0% and 100% seek
  positions.

## Version 2.0.2.14

- Replace unbounded Movies and TV Shows hub rows with fast alphabetical
  previews ending in a BINGIE-styled View All tile.
- Open View All in Kodi's existing complete library view, preserving the
  spotlight and poster-wall experience.
