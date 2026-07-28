# Changelog

Notable changes per release. Versions follow [semantic versioning](https://semver.org);
while the major version is 0, minor bumps may include breaking changes and say so here.

## 0.2.0 (unreleased)

The design brain, plus the fixes found reviewing it.

### Added

- **The design brain**, an optional layer that checks whether a dashboard
  reads well, not just whether it imports. Off with `--design off`, which
  restores byte-identical output.
  - `chartwright brief` prints design guidance to read before writing a spec,
    tuned to an audience preset (`executive`, `analytical`, `operational`).
  - `chartwright advise` reviews a finished spec against 49 rules with stable
    ids. `--fix` applies the safe geometry repairs; `--profile` adds checks
    that need live metadata, such as a time axis on a non-temporal column.
  - `chartwright redesign` decompiles a live dashboard, audits it, applies the
    safe fixes, and writes the repaired spec.
  - `chartwright calibrate` proposes recommended heights from the sizes you
    have polished by hand and absorbed.
  - `check` and `apply` gain `--design off|warn|strict`, defaulting to `warn`.
  - An optional `design` block in the spec sets the audience and suppresses
    individual rules per dashboard or per chart.
  - `~/.config/chartwright/design.yaml` tunes thresholds, disables rules, and
    appends house guidance for a whole deployment.
  - Four MCP tools covering the same ground, taking the server from six to ten.
- `data.unwindowed-history` warns when nothing bounds a timeseries dashboard's
  date range, so every load queries the dataset's full history.
- Apply-time warning when a table or pivot renders more rows than its
  configured height can show, which otherwise hides them behind an inner
  scrollbar with everything else looking healthy.

### Fixed

- `sort_by` on table charts had no effect. It compiled to a sort direction
  with no sort key, so a `row_limit` returned arbitrary rows rather than a
  ranking. It now compiles correctly in both aggregate and raw mode, is
  validated against the dataset like every other reference, and survives a
  decompile, so `chartwright plan` no longer reports a sorted table as
  permanently changed.
- Rows whose widths were left implicit could sum to more than the twelve
  column grid when a row held repeated markdown blocks.
- `chartwright absorb` now writes its report before touching the spec file, so
  a reporting failure cannot follow a silent mutation.
- `chartwright decompile` says so when its dataset index is truncated, instead
  of reporting a dataset it never looked at as unresolvable.
- Duplicate tab titles are rejected at validation rather than producing a
  dashboard whose tabs cannot be told apart.
- Layout sketches are parsed once per holder rather than once per lookup.

### Changed

- A design finding that is withheld because a chart carries a hand-polished
  height is now reported rather than passing silently.
- `--design strict` blocks when the advice cannot be evaluated at all, for
  example because `design.yaml` is malformed. It previously reported zero
  findings and allowed the run.
- Tables and pivots are expected to show at least half the rows their
  `row_limit` requests, up from a quarter. Specs with large explicit row
  limits will see more findings than before.

## 0.1.0

Initial release. Compile a spec into an Apache Superset dashboard, verify
every dataset, column, and metric before building, and check every chart
returns data afterwards. Decompile an existing dashboard back into a spec,
diff a spec against what is live with `chartwright plan`, and restore a
dashboard from an automatic backup.
