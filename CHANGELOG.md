# Changelog

Notable changes per release. Versions follow [semantic versioning](https://semver.org);
while the major version is 0, minor bumps may include breaking changes and say so here.

## 0.2.0 (unreleased)

The design brain, plus the fixes found reviewing it.

### Added

- Table colour rules. `conditional_formatting` on a table, with `apply_to`
  (another column's label, or `"row"`): the rule reads one column and paints
  another, which is how a scorecard colours each number by a status beside it
  when every row has its own goal (Superset 6.1's `columnFormatting`; a pivot
  can only colour a cell by its own value). Also on tables: `hidden` (queried,
  not displayed, e.g. that status), `number_formats` (d3, per label) and
  `sort_ascending` (a fixed row order; an ascending live sort now decompiles
  instead of being reported as a loss). New operator `=` on every colour rule.
- `"paint": "text"` on a colour rule: colour the text (e.g. an arrow) instead of the cell,
  with darker shades of green / amber / red (each >= 4.5:1 on white; Superset's picker
  colours are pastel backgrounds, unreadable as text).
- Select filters take a `default` (values selected on load, e.g. a relative
  "This year" that stays right after 1 January) and `charts` (scope the filter to
  named charts, resolved to slice ids at apply, as on range filters).
- `number_format` on timeseries and categorical bar charts (the value axis;
  it was hard-coded to Superset's smart number, so a rate read `0.9760`, not `97.6%`).

### Fixed

- Changing a chart's dataset in the spec didn't land on re-apply: the in-place
  update sent the new params only, so Superset kept the chart on its old dataset
  (`datasource_id`) while its params pointed at the new one, and `plan` reported the
  chart as changed forever. The update now moves the chart onto its spec dataset.
- A chart, dataset or dashboard name containing `&` (e.g. "Sales & Marketing")
  failed every lookup with HTTP 400 "Not a valid rison/json argument", which
  aborted `apply` at prepare. Flask-AppBuilder's JSON fallback for `q` re-parses
  the decoded value with `parse_qs`, splitting at `&` (and decoding `+` / `%`
  again); those three are now sent as JSON unicode escapes.
- Range colour rules never coloured anything: `between` compiled to the
  literal `"between"`, which is not a Superset comparator in any supported
  release (it is `'< x <'`), so the amber band of every RAG set was dead.
- Colour rules render as solid bands (`useGradient: false`). Superset's
  default fades a `<` / `>` / range colour by the value's distance from the
  threshold, so bands read as shades rather than green/amber/red.

- `"cross_filters": true|false` on the dashboard block. The tool had always
  written `cross_filters_enabled: false` into every dashboard it built, with
  no way to say otherwise; the flag is now spec-owned, decompiles back, and a
  UI toggle shows up in `plan` as `cross_filters_changed`. Default stays
  `false`, so existing specs and the golden bundle are byte-identical.
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
