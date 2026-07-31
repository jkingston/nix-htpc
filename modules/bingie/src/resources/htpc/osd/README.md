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

The timeline marker is intentionally 11 x 11 pixels. Its intrinsic width must
match the `ranges` control height because Kodi preserves a range endpoint's
texture width while scaling only its height. Eleven pixels retains the
existing marker's vertical diameter and the focused timeline geometry while
removing the distortion inherited from the upstream 30 x 30 slider texture.

The icons are original project assets and are covered by the repository
licence.
