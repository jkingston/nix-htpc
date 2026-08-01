# HTPC video OSD icons

These small, fork-owned icons keep the playback controls independent of the
opaque upstream `Textures.xbt` bundle. Kodi uses the PNG files at runtime; the
adjacent SVG files are the readable sources.

Render the runtime assets with:

```bash
magick -background none timeline-marker.svg -depth 8 -strip PNG32:timeline-marker.png
magick -background none chapter-up.svg -depth 8 -strip PNG32:chapter-up.png
```

The timeline marker uses a 20 x 20 transparent canvas containing an 18.5-pixel
disc. Its `ranges` control is padded by the 20-pixel texture's half-width on
both sides, and the service maps timeline progress into that wider coordinate
space. Kodi therefore always takes its full-texture interior path, including
at 0% and 100%, while the marker centre remains aligned with the timeline
endpoints.

The chapter-up chevron is a symmetric, filled, fork-owned image rather than a
font glyph. This makes the cue's optical centre independent of font fallback
and glyph advance metrics. Keep the path filled: the minimal SVG renderer used
for release assets does not reliably rasterize stroked-only paths.

The icons are original project assets and are covered by the repository
licence.
