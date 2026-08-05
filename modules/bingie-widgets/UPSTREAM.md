# BINGIE Widgets provenance

`src/` is a functionally unchanged import of `script.bingie.widgets` 1.0.1
from the BINGIE Omega repository.

- Upstream repository: `https://github.com/matke-84/repository.bingie`
- Archive path: `omega/script.bingie.widgets/script.bingie.widgets-1.0.1.zip`
- Archive commit recorded in its ZIP comment:
  `bd70bf7a65159f474522b346bb0164a2b089d10e`
- Archive SHA-256:
  `68ab4f73f48cc733799a0b2f1496d5a893b2b1a1f6b170c3c5b82d064dd1f4b6`
- `addon.xml` SHA-256:
  `8c9bd5fe40b1027da3888677c5320cde351cbaee517ee95e1073a3c5dd4e8123`

The only local source normalization is removal of trailing whitespace from
text files. There are no behavioural changes from the upstream archive.

The source is vendored so the HTPC build does not depend on the mutable
upstream repository remaining available. Update it by importing a complete
new archive, updating these identities and the package version together, then
running the package and flake checks.
