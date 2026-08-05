# Stable Kodi content routes

Jellyfin creates Kodi library-node directories whose names contain server-side
library identifiers. This reconciler discovers the intended `Anime` and
`Shows` nodes from their Kodi tag filters and publishes stable, relative
aliases named `htpc-anime` and `htpc-tvshows`.

The reconciler also replaces only the `<path>` inside each generated
`nextepisodes.xml` node with the cached local-library provider in the vendored
BINGIE Widgets add-on. Jellyfin continues to own the node label, icon,
content type, library tag, and source directory; the periodic reconciliation
repairs the provider path if Jellyfin regenerates the file.

The skin can therefore use paths such as
`library://video/htpc-anime/nextepisodes.xml` without knowing a Jellyfin
library identifier. Nix owns when the reconciler runs; Jellyfin remains the
owner of the source nodes.
