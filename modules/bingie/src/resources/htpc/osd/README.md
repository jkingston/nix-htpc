# HTPC video OSD icons

These small, fork-owned icons keep the playback controls independent of the
opaque upstream `Textures.xbt` bundle. Kodi uses the PNG files at runtime; the
adjacent SVG files are the readable sources.

Render the runtime assets with:

```bash
magick -background none stop.svg -depth 8 PNG32:stop.png
magick -background none stop-focused.svg -depth 8 PNG32:stop-focused.png
magick -background none timeline-marker.svg -depth 8 -strip PNG32:timeline-marker.png
```

The timeline marker uses a 20 x 20 transparent canvas containing an 11-pixel
disc. This matches Kodi's native `ranges` endpoint allocation instead of
depending on a scale from that 20-pixel allocation to a smaller control. The
visible disc retains the established compact marker size, and the control is
centred on the focused rail so actual and target playheads share one explicit
vertical position.

The icons are original project assets and are covered by the repository
licence.
