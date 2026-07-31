# HTPC video OSD icons

These small, fork-owned icons keep the playback controls independent of the
opaque upstream `Textures.xbt` bundle. Kodi uses the PNG files at runtime; the
adjacent SVG files are the readable sources.

Render the runtime assets with:

```bash
magick -background none stop.svg -depth 8 PNG32:stop.png
magick -background none stop-focused.svg -depth 8 PNG32:stop-focused.png
```

The icons are original project assets and are covered by the repository
licence.
