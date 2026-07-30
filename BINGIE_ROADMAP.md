# HTPC interface programme

This programme turns the in-repository BINGIE fork into a deterministic,
maintainable Raspberry Pi appliance interface. It deliberately favours small
commits over a fast rewrite: every commit must identify one observable change,
carry its own checks, and be safe to deploy or revert independently.

The add-on ID remains `skin.bingie` until a separately tested migration proves
that changing it would not lose Kodi state. Visible identity may change without
changing that compatibility boundary.

## Delivery rules

Each task follows the same review loop:

1. A planning agent inspects the current code and deployed Pi, defines the
   invariant, expected files, automated checks, and manual remote scenarios.
2. An implementation agent makes only that task's changes.
3. At least one independent adversarial agent reviews correctness and failure
   behaviour; another reviews readability, duplication, and deployment impact.
4. The implementation is revised until the reviewers' material findings are
   resolved.
5. Automated checks, a native Pi build, focused remote tests, logs, and
   headless screenshots pass before the commit is accepted.
6. High-risk Home, playback, dependency, and deployment commits are activated
   on the Pi one at a time. Manual feedback is recorded against the exact Git
   revision and NixOS generation before the next such commit is deployed.

A commit is rejected if it:

- mixes refactoring with an unrelated visual or behavioural change;
- creates a second source of truth without deleting or explicitly scheduling
  the old one for removal;
- relies on unexplained numeric control IDs, colours, timings, or geometry;
- adds a compatibility layer where a direct fork-owned path is possible;
- changes seek timing merely to make a presentation refactor easier;
- passes tests but cannot be explained from the resulting source.

Generated screenshots and media-library artwork stay out of Git. Visual
artifacts live under `.artifacts/visual/<revision>/` or temporary storage, with
metadata and hashes when they are used as deployment evidence.

## Invariants that remain in force

- The service owns input classification and playback transactions; the skin
  owns focus, layout, and rendering.
- Cadence-based CEC hold inference remains until physical traces prove a
  defect. Kodi long-press mappings are not a portable replacement.
- Isolated Left/Right taps remain exact short skips. Closely grouped input may
  accelerate; a proven hold enters continuous scrubbing.
- Arrows do not pause merely to reveal the OSD. Scrubbing owns a pause and
  resumes only playback that it paused.
- Seek presentation remains atomic. Do not remove the two publication slots
  until a Pi-tested replacement proves it cannot mix revisions.
- Python never retains or mutates live Kodi window controls.
- Dynamic chapters remain backed by a native Kodi list. They should look and
  navigate like part of the OSD, not be flattened into static skin properties.
- Kodi startup never wakes the TV or announces the Pi as the active CEC source.
- Headless verification must work while the TV is showing another input.

## Baseline

The baseline deployment is Git revision `9730d5d` and BINGIE `2.0.2.9`.

- Skin source: 67,073 XML lines.
- Installed skin: 42 MiB and 173 top-level `1080i` files.
- Warm skin load on the deployed Pi: approximately 0.6 seconds.
- Kodi resident memory on the current Home screen: approximately 362 MiB.
- Existing tests: 97 settings/controller, 16 BINGIE, and 24 Jellyfin tests.
- The generated Skin Shortcuts include is 3,290 lines and contains runtime-only
  menu and row configuration that must be inventoried before removal.
- Kodi can capture a clean 1920x1080 screenshot through local JSON-RPC while
  the TV is on another input, provided `debug.screenshotpath` is configured.

Baseline values are diagnostic, not universal performance promises. Later
measurements must use the same media and UI scenario.

## Milestone 0: make regressions attributable

These tasks change tooling or observability, not interface behaviour.

### M0.1 — Record the programme

Commit this roadmap on its own.

Checks:

- adversarial review for missing user feedback and oversized task boundaries;
- clean Markdown and no production files changed.

### M0.2 — Embed the Git revision in each NixOS build

Set `system.configurationRevision` from the flake revision and expose the
existing Pi toplevel as a flake check without duplicating build logic.

Checks:

- clean-tree evaluation equals `git rev-parse HEAD`;
- dirty-tree evaluation is visibly dirty;
- `nix flake check --no-build --all-systems`;
- a native or remote `nix build .#checks.aarch64-linux.htpc-pi`;
- deployed `nixos-version --configuration-revision` matches the tested commit;
- `/run/current-system` is the exact toplevel path that was accepted;
- after persistence, `/nix/var/nix/profiles/system` resolves to that same
  toplevel and the newest current generation records the tested revision.

### M0.3a — Make the Pi ready for managed screenshots

Create `/tmp/kodi-screenshots` (without a trailing slash in the Kodi setting)
with a declarative systemd-tmpfiles rule and make the managed Kodi settings
service own `debug.screenshotpath`.

Setting the value is not sufficient evidence of readiness. Retry while Kodi is
starting, then query `Settings.GetSettingValue` and mark screenshot readiness
complete only when Kodi returns the exact managed path. Keep this retry and
verification beside the existing managed-setting code rather than adding a
second startup service.

This commit contains no capture helper or artifact handling.
It bumps the managed add-on version in both
`modules/kodi-settings-addon/addon.xml` and `modules/kodi.nix`, so deployed
screenshot-readiness behaviour is independently identifiable.

Checks:

- the tmpfiles directory has the intended path, owner, group, and mode;
- service tests cover an unavailable JSON-RPC endpoint, a rejected write, a
  read-back mismatch, eventual success, and no further writes after success;
- the deployed Pi reports the exact managed `debug.screenshotpath`;
- a direct local JSON-RPC screenshot lands in that directory;
- passive CEC/journal evidence contains no Pi-originated wake, routing, power,
  or active-source action; no active topology/power probe is used.

### M0.3b — Add the capture client and evidence format

Add a small local client that:

- connects over SSH to Kodi's loopback JSON-RPC stream without exposing the
  endpoint on the network;
- uses unique request IDs, socket deadlines, incremental decoding across
  fragmented or concatenated JSON values, response-ID matching, and rejection
  of malformed or trailing responses;
- verifies expected window, focus/readiness evidence, and screensaver/busy
  state across two consecutive polls after animations settle; it fails rather
  than dismissing a screensaver or navigating;
- snapshots the managed screenshot directory, sends only
  `Input.ExecuteAction` with the `screenshot` action, and polls for exactly one
  new or changed `(filename, size, mtime)` candidate whose size and mtime match
  in consecutive polls before a timeout;
- copies, fully decodes, validates, and hashes a complete 1920x1080 PNG before
  accepting it;
- copies the image and an atomic metadata file to
  `.artifacts/visual/<revision>/`;
- records resolution, active window, focus label, NixOS revision, closure,
  add-on versions, byte count, and image hash;
- fails rather than capturing a first-use file picker, unexpected window,
  unready scenario, concurrent screenshot, or partial image.

Focus labels are localized and may be duplicated. Treat them as evidence, not
an invariant; require a control ID only where Kodi exposes a stable one.

The client must not send CEC, wake, power, volume, or active-source actions.
Do not add FFmpeg/KMS capture unless a real OSD layer is missing from Kodi's
fresh screenshot. Real decoded video is not a stable golden; deterministic
review fixtures may use an opaque background.

Checks:

- fragmented and coalesced JSON, response matching, timeout, directory-diff,
  stable-file, PNG, window, readiness, metadata, and failure paths pass without
  a Pi by using a fake transport;
- passive CEC capture plus a `cec-tv-wake` journal cursor shows no Pi-originated
  active-source, routing, view-on, standby, or wake-activation event;
- no active CEC topology/power probe is used; exact TV input identity is manual
  or TV-side evidence when required;
- Home screenshot is 1920x1080 and visually valid;
- an intentionally wrong expected window or readiness condition fails;
- `.artifacts/` is ignored, `git check-ignore` confirms the destination, and no
  captured raster appears in `git status`.

### M0.4 — Define canonical visual scenarios

Add one review manifest with the exact navigation sequence, expected window,
stable focus evidence, readiness condition, and fixture title for:

- Home spotlight, sidebar, Continue Watching, and genre row;
- focused library card, resume progress, and no-parent first item;
- detail Play, Resume, and missing-art states;
- connecting, empty, unavailable, and restored-content states;
- each canonical OSD state defined in Milestone 5.

The manifest defines comparable before/after captures without treating changing
library art as a pixel-perfect golden.

### M0.5 — Add one obvious local verification command

Provide a small, documented command that runs all Python suites, XML checks,
generated-file checks, and Nix evaluation using pinned tools. It must invoke
the existing Nix/package checks, not duplicate their file or command lists.
Avoid a bespoke test framework.

Checks:

- works from a clean checkout;
- does not depend on the host's Homebrew Python or mutable environment;
- reports the failing subsystem clearly.

### M0.6 — Make test deployments and rollback bisectable

Document and, where it remains simple, automate:

1. require a clean committed revision;
2. record the old system closure and mutable Kodi state hashes;
3. build the exact Pi toplevel;
4. activate with `nixos-rebuild test`;
5. run health, log, remote, performance, and screenshot checks;
6. persist with `nixos-rebuild switch --flake` from the exact clean checkout
   only after acceptance;
7. require `/run/current-system`, `/nix/var/nix/profiles/system`, and the newest
   current generation to identify the accepted closure and revision;
8. record revision, closure, generation, add-on versions, and QA result.

Rollback after a failed `test` must use the recorded old closure. Persistent
generation rollback remains a separate operation.

Before automating restoration, classify mutable state:

- writable skin and generated include;
- Kodi add-on database, settings, and auto-update state;
- helper add-on versions and paths;
- caches that should be discarded rather than restored;
- authentication and library data that must never enter an artifact.

Define what is backed up, what is restored, and what intentionally survives.
M1.10 and M1.11 also require a clean-profile probe so rollback is not accidentally
validated only against surviving userdata.

### M0.7 — Add repeatable Pi performance sampling

Capture Home idle, paused OSD, warm scrub, cold preview, playback stop, and a
seek/stop soak. Record CPU, current/peak cgroup memory, temperature, throttling,
coredumps, OOM/rpivid errors, and trickplay-cache cleanup.

For each scenario define warm-up, sample duration, repetition count, cache
state, and aggregation. Reset or derive per-scenario memory peaks rather than
using service-lifetime `MemoryPeak`. Preview latency runs from receipt of the
input token to publication of the matching preview token; p95 uses all accepted
samples across the declared repetitions. Cold network timing remains
observational unless the server and network are controlled.

Initial review thresholds:

- no coredump, OOM, throttling, or rpivid error;
- steady memory no more than 64 MiB or 10 percent above the accepted baseline;
- Home/OSD CPU no more than 15 percentage points above the matching baseline;
- memory returns within 10 percent of baseline 30 seconds after scrubbing;
- warm preview p95 no worse than 20 percent above baseline, with 500 ms as the
  usability ceiling.

Threshold changes require a documented measurement, not intuition.

### M0.8 — Audit upstream source, assets, and dependencies

Add a stable report for:

- upstream-owned immutable binaries;
- fork-owned textual source;
- modified, fork-only, and intentionally pruned paths;
- every non-core add-on imported by `addon.xml`;
- which dependency is provided by Nix and which still drifts in Kodi userdata.

Keep upstream `Textures.xbt` opaque until source artwork and licensing are
known. New fork-owned images should be loose files with provenance rather than
silently replacing the archive.

The fixed upstream hash guarantees content but not future availability.
Acceptance requires a trusted binary cache, controlled immutable mirror, or
documented recovery artifact for every immutable upstream input.

This establishes the single audit tool and report schema. Later pruning and
asset work extend the same mechanism rather than creating another report.

### M0.9 — Package Home-critical dependencies

Do not begin the declarative Home cutover while its runtime can drift outside
the recorded closure. Use the M0.8 inventory and package each required family
in a separate commit:

- `script.bingie.helper`;
- `script.bingie.toolbox`;
- `script.bingie.widgets`;
- `plugin.video.tmdb.bingie.helper`;
- required image resources and autocomplete support.

Skin Shortcuts may remain temporarily packaged until M1.10 removes it. Every
dependency commit checks the installed add-on ID/version and a fresh-profile
startup. Optional helpers that survive later reachability analysis remain for
M6.3.

## Milestone 1: make Home deterministic

Complete the live Pi inventory before M1.2. Record menu order, row order,
actions, paths, hub destinations, view IDs, relevant skin settings, and the
generated include hash. Do not commit credentials, tokens, or personal server
addresses.

### M1.1 — Characterise current Home

Add a small manifest and contract tests for the intended sidebar, Home rows,
hub actions, focus targets, and lack of Movies/TV submenus. This is a no-visual-
change commit.

Manual evidence:

- Home spotlight with Play focused;
- sidebar open;
- each current Home row;
- Movies and TV destinations;
- Search and power menu.

### M1.2 — Delete unreachable hub families

For every hub family that M1.1 proves unreachable, delete its window, includes,
settings, and generated-group expectations in a separate no-visible-change
commit. Do not carry dead hubs into design or state work.

### M1.3 — Add fork-owned sidebar content

Introduce one clearly named menu include and switch only the sidebar to it.
Keep row generation unchanged for this commit.

Acceptance:

- complete sidebar on cold start;
- Left/Right between Play and More Info does not open the sidebar;
- Right on Movies/TV goes directly to its destination;
- Back and focus restoration are unchanged;
- no duplicate menu source is referenced by the sidebar.

### M1.4 — Add fork-owned Home row declarations

Represent each row once with an explicit ID, label, media path, and layout.
Switch Home rows from generated widget groups without changing their order or
content.

Acceptance:

- Continue Watching, next-episode, recent, and genre rows match the manifest;
- empty optional rows collapse cleanly;
- row focus and Back restoration survive a restart;
- cold and warm Home screenshots show no missing menus.

### M1.5 — Add fork-owned Search content

Replace only the generated Search groups. Preserve the captured actions and
make the available destinations explicit in the same Home manifest.

Acceptance:

- Search opens with a useful initial focus and Back returns to Home;
- Search no longer references a generated group.

### M1.6 — Add fork-owned power content

Replace only the generated power group. Power actions must be deliberate
appliance actions with confirmation where destructive.

### M1.7 — Add fork-owned Movies hub content

Replace the Movies hub groups and genre destination without touching TV,
Anime, or My List in the same commit.

Acceptance:

- direct sidebar entry, expected rows, genre row, title details, and Back;
- Right on the sidebar never exposes the old submenu;
- empty optional rows do not create focus holes.

### M1.8 — Add fork-owned TV hub content

Replace the TV hub groups and genre destination. Verify TV show, season, next
episode, recently added, and Back paths.

### M1.9 — Replace remaining retained hub groups

Migrate Anime, My List, New, or other groups only when M1.1 proved them
reachable. Use one commit per independently reachable hub; M1.2 has already
deleted unreachable families.

### M1.10 — Remove Skin Shortcuts runtime generation

Remove the Home `buildxml` action, add-on dependency, shortcut editor entry,
generated-include preservation, and now-unreachable shortcut configuration.
Delete superseded paths instead of leaving a disabled second system.

This is an atomic NixOS-generation checkpoint across skin source, dependency
closure, writable-file handling, and rollback state, not an ordinary XML edit.
The removals belong in one commit because mixing its old and new packages is
invalid; unrelated Home polish does not.

Acceptance:

- zero runtime `skinshortcuts` references;
- fresh profile and upgraded Pi both show a complete Home;
- no “unable to build shortcuts” notification;
- mutable generated file is no longer needed for correct startup;
- rollback to the previous generation restores its complete generated Home.

### M1.11 — Make the skin installation immutable

Install the fork through Kodi's Nix add-on closure and remove the writable
userdata copy using a recoverable one-generation migration. Remove the
migration in a following cleanup commit after the Pi has proven the store skin
is selected.

Acceptance:

- Kodi reports the store-provided fork and correct version;
- no userdata skin shadows it;
- restart and rollback select the expected skin;
- closure alone is sufficient to reproduce Home source.

### M1.12 — Delete the completed skin migration

Delete the backup/migration code only after M1.11 has passed a reboot and manual
Home check. This commit must contain no interface changes.

## Milestone 2: establish one visual language

Each visual task gets before/after screenshots at identical window and focus
states. Token introduction and visible restyling remain separate commits.

### M2.1 — Add named design tokens

Create one include for appliance colours, surfaces, opacity, typography roles,
spacing, geometry, focus, motion durations/easing, and progress presentation.
Initially map tokens to accepted current values so this is a structural change.

Each appliance token is defined exactly once. A migrated surface references
only the token layer: persistent `Skin.String(...)` colour/timing
customisations cease to be authoritative there. Contract tests reject new
hard-coded colours or timings outside the token file.

### M2.2 — Unify focus and card primitives

Create one focused/unfocused card treatment with a stable geometry and explicit
selection indication. Migrate Home cards first.

Acceptance:

- focus never changes the progress-marker geometry;
- selected and unselected cards remain distinguishable in screenshots;
- rapid navigation has no overlapping or stale focus decoration.

### M2.3 — Unify watched and resume progress

Replace copied Home progress implementations with one wider, restrained
Netflix-like track/fill primitive. Preserve actual resume semantics.

Acceptance:

- 0, partial, nearly complete, and watched states;
- no progress bar on unstarted media;
- correct clipping, contrast, and focus behaviour.

### M2.4 — Polish Home typography, spacing, and motion

Apply the design roles to spotlight, buttons, metadata, row headings, and
sidebar. Keep motion short and purposeful; do not animate cursor geometry.

Acceptance:

- 1080p safe areas, long title/plot, missing clearlogo, and dense genre text;
- Play remains visually primary;
- sidebar and spotlight controls read as one system.

### M2.5 — Remove inherited visible identity

Remove remaining BINGIE branding from settings, startup, and appliance chrome.
Do not remove title-specific media logos. Keep `skin.bingie` internally.

Delete dead branding controls, references, settings strings, and loose assets
where reachability and provenance permit; do not hide them behind another
visibility condition. Any new icon or image must have source and licence
recorded.

## Milestone 3: own browse and title-detail UX

### M3.1 — Characterise active library views

Record which landscape, poster, square, season, and episode views the Pi
actually uses. Add focus-graph checks for those files before restyling them.

### M3.2 — Lock the no-parent-directory contract

The service already applies `filelists.showparentdiritems=false`. Add its
regression contract and delete skin settings or controls that contradict that
single source of truth; do not reimplement the working behaviour.

Acceptance:

- Movies, TV, genres, seasons, and episodes start with real content;
- Back still returns one level;
- no empty leading focus target.

### M3.3 — Migrate the active landscape library

Reuse the card, progress, and focus primitives in only the active landscape
view. Capture unplayed, partial, watched, focused, and missing-art cards.

### M3.4 — Migrate the active poster library

Reuse the same primitives without changing view selection or content actions.

### M3.5 — Migrate the active square library

Reuse the same primitives without assuming the poster layout's aspect ratio or
focus geometry.

### M3.6 — Migrate season and episode libraries

Apply the primitives to seasons and episodes and verify show -> season ->
episode -> Back focus restoration.

### M3.7 — Preserve detail entry and restore action focus

Play/Resume focus and the detail-to-video loading shield already work at the
baseline. Characterise them, preserve their action matrix, and change only
focus restoration after returning from secondary content.

Acceptance:

- unplayed movie, resumable movie, TV show, season, and episode;
- no default focus on Like/Dislike;
- playback loading never reveals Home between detail and video.

### M3.8 — Simplify the detail hero and actions

Apply the design system to title art and the primary/secondary action hierarchy.
Do not touch cast rows or playback action resolution in this commit.

### M3.9 — Simplify detail metadata and plot

Give year, certification, duration, genres, status, and plot one clear hierarchy
with long-text and absent-value cases.

### M3.10 — Simplify cast and related rows

Restyle and navigate cast/related content independently from the hero. Verify
entry, focus restoration, empty rows, and Back.

### M3.11 — Add detail missing-data fallbacks

Handle missing clearlogo, poster, fanart, plot, ratings, cast, and related
content without blank focus targets. Preserve the existing
playback-resolution action matrix until it receives its own characterised
refactor.

### M3.12 — Polish sort, filter, and browse focus restoration

Make overlays remote-first, dismissible with Back, and clear about the active
choice. Verify Movies -> genre -> title -> detail -> Back and the equivalent TV
path.

## Milestone 4: truthful system states

### M4.1 — Define the Jellyfin lifecycle contract

Inventory the existing `jellyfin.connected`, `jellyfin_online`,
`jellyfin_startup`, and `jellyfin_sync` properties. Extend Jellyfin's existing
lifecycle publisher only if connecting, offline, and authentication failure
cannot be distinguished. Do not add duplicate network polling.

### M4.2 — Add one shared state component

Define exclusive precedence:

1. unavailable or authentication error;
2. connecting or syncing;
3. online and settled but empty;
4. content.

Optional empty rows disappear; a genuinely empty hub explains the whole state.
Offline messaging must not obscure usable synced content.

### M4.3 — Apply states to Home

Add connecting, empty, and unavailable behaviour to declarative rows. Verify
startup, reconnect, and restoration of content.

### M4.4 — Apply states to Search

Distinguish initial, searching, no results, unavailable provider, and results
without trapping focus or replacing a useful local result with an offline
message.

### M4.5 — Apply states to the Movies hub

Capture connecting, empty, unavailable-without-synced-content, and restored
content.

### M4.6 — Apply states to the TV hub

Use the same component and precedence without copying the state logic.

### M4.7 — Apply states to other retained hubs

Migrate Anime, My List, or other hubs retained by M1.9, one per commit.

### M4.8 — Apply states to the Movies library

Use the shared state contract without adding state logic to its layout.

### M4.9 — Apply states to the TV library

Reuse the Movies-library state boundary and keep TV-specific content rules in
its data source.

### M4.10 — Apply states to genre libraries

Cover empty and unavailable movie and TV genres without maintaining two state
implementations.

### M4.11 — Apply states to seasons and episodes

Preserve the show hierarchy and Back path when a season or episode source is
loading, empty, or unavailable.

### M4.12 — Apply states to detail metadata and rows

Handle delayed or failed metadata, cast, and related content independently so a
secondary provider failure does not hide playable local media.

### M4.13 — Handle playback-resolution failure

Give the existing detail playback action matrix an explicit resolving, failed,
retry, and cancelled presentation without exposing Home underneath it.

### M4.14 — Unify busy and playback-loading presentation

Keep the existing shield from detail to video, but use the same visual language
for slow library and playback transitions. Never route focus to a loading
control.

## Milestone 5: replace the inherited video OSD

The production cutover is intentionally late. Prototype and state-model commits
must prove themselves without changing playback first.

The intended remote contract is:

| Context | Input | Outcome |
| --- | --- | --- |
| Fullscreen, playing | OK | Pause, open OSD, focus transport |
| Fullscreen, already paused | OK | Open OSD, remain paused, focus transport |
| Fullscreen | Left/Right | Open OSD with timeline visibly focused and stage one exact +/-10-second target |
| Fullscreen | Up/Down | Open OSD, preserve play/pause state, focus transport, and do not chapter-skip |
| OSD transport | OK | Toggle play/pause |
| OSD transport | Right | Focus the timeline without seeking |
| OSD timeline | Left/Right | Continue exact taps, grouped taps, or proven-hold scrubbing |
| OSD timeline, idle | Down | Return to transport; transport Left/Right then navigate controls |
| Staged short seek | Quiet timeout | Perform one player seek and clear the staged target |
| Promoted scrub | OK / Back | Commit / cancel the target |
| OSD timeline | Up | Open the ready chapter rail when chapters exist; otherwise move to top controls |
| Chapter rail | Down/Back | Cancel chapter browsing and return to timeline |
| Chapter rail | Up | Cancel chapter browsing and move to top controls |
| Top controls | OK | Activate the focused subtitle, audio, video, stop, or disc-menu action |
| OSD or child dialog | Back | Return one UI level without leaving playback |
| Fullscreen with OSD closed | Back | Leave fullscreen and stop non-live video |

Down is the non-seeking route from the timeline back to transport. If physical
testing shows that route is surprising, change that single transition in its
own commit rather than changing the surrounding seek model.

Any edit to `htpc-home.nix`, a Kodi keymap, CEC repeat timing, or CEC activation
is a standalone commit. It may not be bundled with Python focus logic or OSD
XML.

Canonical visual states:

1. paused with transport focused;
2. timeline focused with actual progress;
3. active seek with distinct actual and target positions;
4. trickplay frame above and following the target;
5. chapter rail with a middle chapter focused;
6. a top control focused with transport/timeline unhighlighted.

Captures wait for animations and observe the same slot/revision in two
consecutive polls. The clean headless Home path proves GUI capture, not decoded
GBM video pixels. OSD comparisons therefore use an opaque review fixture, while
real playback remote checks remain mandatory for decoder and CEC behaviour.

### M5.1 — Add the inactive fork-owned OSD include

Build one readable video-only surface: metadata, transport, timeline, top
controls, preview, and chapter layer. It is structurally tested but not
referenced by production playback yet. Prefer parameterised geometry to
hundreds of generated anchors, but keep the current production renderer until
the Pi proves an atomic alternative.

### M5.2 — Add a deterministic OSD review window

Use the M5.1 layout include with fixture properties and an opaque backdrop. The
driver may set properties, activate the review window, set focus, capture, and
close it. It must not open media, route CEC, or change TV power/input.

### M5.3 — Refactor the focus transition model

Represent only `hidden`, `transport`, `timeline`, `chapters`, and `top`.
Python may retain a deferred post-transaction destination, not duplicate Kodi's
live focus. Table-test every direction, OK, Back, chapter exit, resume, reset,
repeat-train boundary, top-control child dialog, and return path. Do not change
seek timing.

### M5.4 — Harden transaction recovery

On fatal error, service restart, stop, end, or media replacement:

- close chapter UI;
- retire outstanding work;
- resume only a controller-owned pause;
- clear presentation;
- ignore callbacks belonging to old media.

Inject failures and delayed/missing callbacks in tests before production OSD
cutover. Because the fullscreen keymap depends on the service, also provide and
test a bounded availability strategy: the input loop recovers or is restarted
within an explicit maximum time, and an owned pause is always unwound. A dead
service must not leave Select, Back, or seeking permanently inert.

### M5.5 — Cut video playback over to the fork-owned OSD

`VideoOSD.xml` references one owned video surface. Keep a separate passive seek
HUD only while the OSD is closed.

Acceptance with the physical remote:

- isolated Left/Right, rapid taps, hold/release;
- every row of the remote contract above;
- subtitle, audio, video-settings, stop, and disc-menu activation plus Back
  restoration from their child dialogs;
- non-seekable media and live-safe conditions;
- one progress rail and one authoritative target cursor;
- no target/current flicker at decoder handoff;
- OSD does not leave playback running behind Home after Back.

Trickplay preserves latest-target-wins coalescing, one foreground exact-preview
lane, bounded neighbour prefetch, bounded sprite/frame/chapter caches,
stale-token rejection, and cleanup on stop. Failure must never block seeking or
show a frame belonging to another target or media item.

### M5.6 — Integrate the native chapter rail visually

Keep the dynamic `WindowXMLDialog` list but remove the secondary-modal look.
Align it over the OSD timeline with the OSD visible beneath it.

- Left/Right previews chapters without seeking.
- OK commits.
- Down or Back cancels to timeline.
- Up exits toward top controls.
- Opening, browsing, and cancelling remain one pause-owned transaction.

The chapter publisher retains its all-frames-ready barrier: before that barrier
the rail is unavailable rather than partially populated. Test the unavailable
state, empty/missing source frames, boundaries, media stop, service restart,
and identity change.

### M5.7 — Re-measure acceleration and scrubbing

Only after the stable OSD is deployed, capture physical CEC traces and measured
target progression for isolated taps, grouped taps, and holds. Change one
threshold or curve per commit, with examples and before/after data. A brief
quiet period resets the next press to a fresh 10-second seek; direction reversal
resets the ramp; button release stops continuous integration immediately.

### M5.8 — Cut over to a minimal versioned playback protocol

After a soak period, atomically switch skin, settings service, and Jellyfin to
the minimal fields actually consumed by the owned OSD. Keep the previous
presentation files in place but unreachable for this commit.

This is a cross-package deletion checkpoint and must roll back atomically as a
whole NixOS generation.

### M5.9 — Delete the legacy skin presentation

Delete the now-unreachable inherited OSD includes and references. This commit
does not change the protocol or input behaviour.

### M5.10 — Delete obsolete controller paths

Remove unused service properties and compatibility branches proven dead by the
M5.8 protocol.

### M5.11 — Delete obsolete chapter presentation paths

Remove only the old chapter properties, dialog branches, and presentation paths
superseded by the integrated native rail.

### M5.12 — Delete obsolete generated preview anchors

Remove the generator, generated blocks, and generator tests only when the
parameterised replacement has passed the Pi soak and visual review.

## Milestone 6: simplify and own the fork

### M6.1 — Produce a reachability inventory

Combine static references, Kodi conventional filenames, settings, and observed
runtime window/view use. Lack of an `rg` hit is not proof that a Kodi skin file
is unused.

### M6.2 — Remove one proven-dead compatibility family per commit

Begin with confirmed LibreELEC/OpenELEC-only windows. Treat music, PVR, games,
weather, touch, mouse, and view layouts as separate decisions with separate Pi
checks.

### M6.3 — Package remaining required add-ons declaratively

After Skin Shortcuts removal and reachability analysis, package each remaining
optional helper separately or remove imports whose call sites have been
deleted. Extend the M0.8 dependency report. Fresh-image and rollback behaviour
must not depend on Kodi auto-update state.

### M6.4 — Close owned-asset provenance gaps

Complete the M0.8 source/licence/hash records for every retained or new visible
asset. Add a texture build pipeline only when it is simpler and more auditable
than loose fork-owned files.

### M6.5 — Extend the upstream report for pruning

Extend and rerun the M0.8 report so it identifies modified, unchanged,
binary-owned, fork-only, locally pruned, and newly introduced upstream paths
without applying changes automatically.

## Parallel execution boundaries

Safe parallel work:

- revision identity, upstream audit, and performance documentation when their
  file ownership is disjoint;
- inactive Home content, OSD review fixtures, and lifecycle research after
  their property/ID contracts are frozen;
- automated test expansion and manual scenario preparation;
- separate library layout families once shared design primitives are stable.

Must remain serial:

- edits to `flake.nix`, `modules/kodi.nix`, `Includes.xml`, shared design
  primitives, or a cross-package skin/service/Jellyfin protocol;
- production Home and OSD cutovers;
- Pi activation, remote verification, and persistent switching;
- deletion of a legacy path and the migration that replaces it.

No two implementation agents edit the same production file concurrently. Work
may be prepared in parallel, but the primary agent rebases each task on the
accepted previous commit and reruns its complete checks.

## Completion criteria

The programme is complete when:

- Home, rows, sidebar, hubs, dependencies, and skin source are reproducible from
  the flake without runtime generation or shadow copies;
- playback uses one fork-owned OSD with stable seek/trickplay and an integrated
  native chapter rail;
- all requested remote interactions have an explicit, tested transition;
- loading, empty, unavailable, resume, watched, and missing-data states are
  coherent across Home, browse, detail, and playback;
- visible identity and owned assets have documented provenance;
- measured Pi performance remains within accepted budgets;
- every milestone has automated checks, headless visual evidence, physical CEC
  checks where relevant, and a tested rollback boundary;
- the final source has no known duplicate presentation or obsolete migration
  path.
