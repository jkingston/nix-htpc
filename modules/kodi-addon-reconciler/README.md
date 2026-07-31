# Kodi add-on reconciler

This fixed-capability root helper switches selected Kodi add-ons between their
userdata copies and optional immutable packages. It has no command-line or
environment configuration: Nix writes the complete allowlist and identities
into its immutable closure.

The production roots are:

- active: `/home/htpc/.kodi/addons`
- backup: `/var/lib/nix-htpc/kodi-addon-backups` (`0700`, `root:root`)

For every add-on, preflight validates the exact `addon.xml` version and
manifest SHA-256. This proves the declared manifest identity, not the entire
add-on tree. It rejects symlinks, non-directories, non-regular manifests,
unexpected XML, conflicting active/backup copies, and cross-filesystem moves.
All add-ons are preflighted before the first atomic no-replace rename.

With `managed = null`, an active copy is untouched and a backup copy is
restored. With a managed package, an active userdata copy is backed up so Kodi
can use the immutable package. Reverting that spec to `managed = null`
restores the original directory, including its ownership, modes, payload, and
inode. `userdata/addon_data` is never in scope.

The helper runs before BINGIE skin synchronization in `greetd.preStart`. A
preflight failure therefore keeps Kodi stopped. Roll back over SSH to the
unmanaged generation after resolving any reported conflict; the rollback
restores valid backups in the configured spec order.
