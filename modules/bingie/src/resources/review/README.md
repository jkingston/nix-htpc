# OSD review frames

These deterministic frames exist only for the headless video-OSD review
window. They make a stale or incorrectly positioned trick-play image obvious
without depending on Jellyfin, decoded video, or cache contents.

The PNG files are rendered from the adjacent SVG sources:

```bash
magick -background none seek-25.svg seek-25.png
magick -background none seek-75.svg seek-75.png
```

They are original project assets and are covered by the repository licence.
