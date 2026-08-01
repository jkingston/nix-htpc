# HTPC BINGIE fork

This directory owns the reviewable source for the HTPC's `skin.bingie` fork.
It starts from upstream BINGIE 2.0.2 and keeps the add-on ID unchanged so Kodi
retains the existing skin settings and generated menu shortcuts.

`src/` is the authoritative XML, language, shortcut, and other textual source.
`upstream-assets.nix` fetches only explicitly allowlisted immutable fonts,
compiled media, resources, and raster artwork from the hash-pinned upstream
archive. The unused startup videos and splash image are not assembled.
`default.nix` combines those inputs without source patches or build-time text
rewrites.

The greetd pre-start in `modules/kodi.nix` validates and synchronises the
assembled skin while Kodi is stopped. It deliberately preserves the user's
runtime-generated `1080i/script-skinshortcuts-includes.xml`.

## Fork behaviour

The local source contains the previously deployed Home, library, information
screen, playback-start, playback-exit, chapter, remote-navigation, and OSD
polish. The managed service in `modules/kodi-settings-addon` owns gesture
classification and playback intent; the skin owns focus, layout, and rendering.

During a managed seek, the service publishes a finite, clamped semantic view
model. BINGIE renders its target fill and marker using Kodi ranges controls and
positions the trick-play card with deterministic one-percent anchors. No Python
code retains or mutates live Kodi window controls.

See `UPSTREAM.md` for import provenance and asset boundaries, and
`FORK_CHANGES.md` for the maintained behavioural delta.

## Dependency evidence

`audit/dependency-inventory.json` records the seven non-core imports, their
sanitized userdata or Nix-closure observations, and optional add-ons referenced
by the skin. Generation B pins `script.bingie.helper` in Nix, resolves
`script.module.simplejson` transitively, and leaves the other six mandatory
imports in userdata. Validate the report headlessly against the current fork:

```bash
python3 -B tools/bingie_dependency_inventory.py check
```

The same tool can passively capture version and manifest-hash observations from
explicit locations. It filters output to the seven mandatory imports and never
invokes Kodi, shells out, or changes the scanned files:

```bash
python3 -B tools/bingie_dependency_inventory.py capture \
  --scope userdata \
  --root /path/to/kodi/addons \
  --path-list /path/to/addon-xml-paths.txt
```

Each `--root` is either one add-on directory or a directory whose immediate
children are add-ons; capture does not recurse deeper. Each non-comment line in
a `--path-list` must be an absolute path to an add-on directory or its
`addon.xml`.

Capture output intentionally omits host paths and Kodi's enabled state; the
committed runtime-enabled observations are separately reviewed evidence. A null
`nix_closure` is backed by a complete scan of all add-on manifests in every
recursive requisite returned by
`nix-store --query --requisites /run/current-system`; the report records only
the sanitized system-closure basename and matching mandatory add-on IDs.

The report, validator, and audit tests are inputs only to the dedicated
`bingie-dependency-audit` flake check. They are deliberately not inputs to the
runtime BINGIE package, so an audit-only change cannot alter the skin
derivation, restart Kodi, or rerun the writable-skin synchronization. Applying
this decoupling changes the runtime derivation once; later audit-only changes
do not.

## Updating upstream

1. Fetch and hash the new upstream archive.
2. Import all mutable/textual source into `src/`, retaining local changes as a
   normal source merge.
3. Review the explicit binary paths in `upstream-assets.nix`.
4. Update provenance and fork-change documentation.
5. Run the Python, XML, generator, flake, native Pi build, CEC, visual, and
   playback-soak checks before deployment.

The bundled fonts and artwork are retained byte-for-byte from upstream. Their
individual redistribution terms still need a separate audit before publishing
binary releases of this fork.
