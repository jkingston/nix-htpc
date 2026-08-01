# HTPC UI contract

This directory is the neutral interface between the Kodi skin, the input and
seek service, and content providers. It contains no Kodi, Jellyfin, BINGIE, or
NixOS implementation details.

- `contracts/home.json` defines stable Home destinations, row roles, and focus
  identifiers. Providers resolve the logical routes into Kodi content.
- `contracts/playback.json` defines the Window properties, atomic publication
  rules, control identifiers, and notifications shared by the playback service
  and skin.
- `contract.py` strictly loads and validates either contract without third-party
  Python dependencies.

Nix packages these files but must not rewrite them. Consumers should validate
the contract during their own tests and bind to its symbolic names. Machine or
library-specific values belong in provider configuration, not these contracts.

Contract changes are compatibility changes. Additive changes retain the schema
version; removing or changing an existing name, type, or meaning requires a new
schema or protocol version and a coordinated consumer migration.
