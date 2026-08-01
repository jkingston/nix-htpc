# Stable Kodi content routes

Jellyfin creates Kodi library-node directories whose names contain server-side
library identifiers. This reconciler discovers the intended `Anime` and
`Shows` nodes from their Kodi tag filters and publishes stable, relative
aliases named `htpc-anime` and `htpc-tvshows`.

The skin can therefore use paths such as
`library://video/htpc-anime/nextepisodes.xml` without knowing a Jellyfin
library identifier. Nix owns when the reconciler runs; Jellyfin remains the
owner of the source nodes.
