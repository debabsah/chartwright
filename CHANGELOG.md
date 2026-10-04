# Changelog

Notable changes per release. Versions follow [semantic versioning](https://semver.org);
while the major version is 0, minor bumps may include breaking changes and say so here.

## Unreleased

### Added

- `dashboard.css`: the dashboard's CSS, the same thing as Superset's own "Edit CSS",
  written into the import bundle's `css` field (which 4.1.4, 5.0.0 and 6.1.0 all import).
  Decompiled (blank CSS reads back as omitted) and compared by `plan` (`css_changed`).
  Spec-owned like the title: CSS edited in the UI shows up in `plan`, and `apply` replaces
  it, as it always did when the spec had none.
- Colour rules take any `#RRGGBB` besides `green` / `amber` / `red`, e.g.
  `"color": "#0057B8"`, painted as written for cells and text alike (stored upper case).
  The names keep their shades. Decompile reads a named shade for the rule's paint back as
  its name and any other hex as itself, where it used to drop the rule as a loss.
- Chart display options, each optional and written only when set, so an existing spec
  builds the same bundle. All decompile back and show up in `plan` when changed in the UI.
  - Axes on line, bar, area, scatter, categorical bar and mixed charts: `x_axis_title`,
    `y_axis_title`, `y_axis_min`, `y_axis_max`, `y_axis_truncate` (fit the axis to the
    data instead of including zero) and `y_axis_log`; a mixed chart's second axis takes
    `y_axis_title_secondary`, `y_axis_min_secondary`, `y_axis_max_secondary` and
    `y_axis_log_secondary`. A histogram takes the two titles.
  - `show_value` (values on the bars or points), `stack` (`true`, or `"stream"` and
    `"expand"` where the chart offers them), `only_total` and `contribution` (`"row"`
    with `stack` is a 100 % stacked chart). A mixed chart's queries take `show_value`,
    `stack` and `only_total` (6.1+) each.
  - `series_limit`, `series_limit_metric` and `series_limit_ascending`: the top N series
    of a `groupby`, on the timeseries charts, the categorical bar and each mixed query.
  - `show_legend`, `legend_position` (`bottom`, `left`, `right`) and `legend_type`
    (`plain`), on the timeseries, bar, mixed, pie and funnel charts; a heatmap takes
    `show_legend`.
  - `category_sort` (`"asc"` or `"desc"`) on a categorical bar: bars in the order of
    their categories, such as hours, instead of by the first metric.
  - `time_range` on every chart type, e.g. a "Last 30 days" KPI or table.
  - Trendline KPIs: `compare_lag` and `compare_suffix` ("+4 % vs last month"),
    `trend_color` (green, amber, red or `#RRGGBB`) and, on Superset 6.1+, `subtitle`.
  - Tables: `page_length`, `show_totals`, `search_box`, and per label `column_align`,
    `column_widths` and, on Superset 6.1+, `column_headers`.
  - Pivots: `aggregate_function` (Average, Median, Sum as Fraction of Total, ...),
    `row_order` and `column_order` (by label or by value), `row_subtotals`, `transpose`
    and `metrics_layout`.
  - Heatmaps: `show_values`, `color_scheme` (Superset's sequential schemes),
    `number_format`, `show_percentage` and `normalize_across`. Pie, funnel and treemap:
    `label_type` and `number_format`; pie also `show_total` and `labels_outside`.
  - Line: `markers`, `marker_size`, `area` and `opacity`; area: `markers`, `marker_size`
    and `opacity`; scatter: `marker_size`.
- The design review knows the new options: a vertical bar sorted by category that labels
  every category (hours, ranks) passes `chart.vbar-categories`, and the fix for one that
  drops labels is `x_label_every`, not a flip to horizontal; a paged table needs room for
  one page; `series_limit` quiets `chart.series-limit`; a trendline with its own
  `time_range` quiets `chart.trend-grain`.
- Dashboard settings in the spec: `color_scheme`, `description`, `certified_by`,
  `certification_details`, `published` (`false` for a draft), `refresh_frequency`
  (seconds), `filter_bar_orientation` (`"horizontal"` puts the bar above the charts; 4.1.4
  and 5.0.0 need Superset's `HORIZONTAL_FILTER_BAR` flag), and, on Superset 6.1 only,
  `show_chart_timestamps` and `tags`. `plan` names each one that differs in a new
  `dashboard_settings_changed` list. Omitted `tags` are left alone by `apply` and `plan`
  alike; `[]` clears them.
- Chart settings: `color_scheme` (on the chart types whose panel has one), `description`
  (viewers open it with "Show chart description"), `certified_by`,
  `certification_details`, `cache_timeout`, `display_name` (a shorter title shown on the
  dashboard card, Superset's `sliceNameOverride`) and `tags` (6.1 only). A re-apply
  updates description, certification and cache timeout on existing charts too.
- `check`, `apply` and `plan` hold a spec to the instance's Superset release. When the
  spec uses a release-specific field, they read the version (`/version` on 6.1, the
  sign-in page on 4.1 and 5.0) and refuse `tags` before 6.0.0 and
  `show_chart_timestamps` before 6.1.0 with a resolve-stage error, before anything is
  written: `superset_version_too_old`, or `superset_version_unknown` when the instance
  doesn't say. Fields older releases ignore (`x_label_every`, a trendline's `subtitle`,
  `column_headers`, a stacked mixed query's totals-only labels) come back as
  `version_warnings`. `--superset-version` (the MCP tools' `superset_version`) states
  the release instead; `compile --superset-version` runs the same check offline.
- A colour scheme name Superset doesn't ship gets a `narrative.color-scheme` warning (with
  a did-you-mean for a case slip); a deployment's own registered schemes are accepted.
  The design brain's version is now 4.
- `annotations` on line, bar, area, scatter and mixed charts: goal and trend lines, as
  Superset's FORMULA annotation layers, e.g. `{"name": "Goal", "value": 80, "style":
  "dashed"}`, with colour, width and opacity. Decompile keeps formula layers and names
  any other layer type as a loss, where it used to drop them silently.
- Custom SQL metrics, `SQL(100.0 * SUM(a) / NULLIF(SUM(b), 0)) AS Rate`, anywhere a
  metric goes, and custom SQL chart filters, `{"sql": "amount > 0 OR refunded"}`.
  `check` cannot see the columns inside SQL, so it lists them under `unchecked_sql`;
  apply's data check runs them. UI-built SQL metrics and SQL filters now decompile, where
  they used to be dropped as losses.
- Native filter controls: `description` on every filter; `dependencies` (cascading, by
  filter name); on value pickers `search_all_options`, `inverse_selection`,
  `sort_metric`; on value pickers and sliders a pre-filter (`pre_filter` conditions, and
  `time_range` with `time_column`); `charts` scoping on the time range picker; and two new
  filter types, `time_grain` and `time_column`.
- Layout: `{"header": "Revenue", "size": "large"}` and `{"divider": true}` entries between
  rows (Superset nests headers and dividers beside rows, never inside one), and
  `{"row": [...], "background": "white"}` for a row on a card.
- Design defaults: `advise --fix` (MCP `fix_spec`) fills sensible values into display
  fields the author left unset and writes them into the spec, where the diff shows them.
  Nothing changes at compile time: compile, `plan` and decompile are as before, and a
  spec that was never fixed builds the same bundle. Eight `default.*` rules, each an
  `info` finding with a fix that `check` and `apply` list too:
  - `x_label_format` from the time grain (`%b %Y` monthly, `%Y` yearly, `%d %b` daily
    or weekly over 365 days or less);
  - `compare_suffix` from the grain and `compare_lag` ("vs previous month");
  - `,.0f` for charts whose metrics are all COUNT or COUNT_DISTINCT;
  - on tables, `cell_bars: false` with id, code, year or zip columns, `page_length` set to
    the rows that fit beside the page controls when an explicit `row_limit` outgrows the
    panel, and `search_box` on raw tables of more than 20 rows when no row loses its
    place to the search bar;
  - `show_legend: false` on a single series the title names, `show_value` on a bar of 12
    bars or fewer: at least half the page wide, or on a horizontal bar, tall enough to
    space its labels.
  None writes Superset's own value; the thresholds are audience parameters
  (`search_min_rows`, `value_label_max_bars`, `value_label_min_width`, `page_min_rows`,
  `day_label_max_span_days`), tunable in `design.yaml`. The grid and value-label numbers
  were measured on rendered Superset 4.1.4, 5.0.0 and 6.1.0 (DESIGN-BRAIN §17).
- `design.filled` records, per chart, each field `--fix` filled and the value it wrote.
  While the chart still holds that value, `--fix` keeps it up to date as the chart changes
  and removes it when it stops applying. A field you write is never touched. Edit a filled
  value and it is yours; delete it and it stays deleted (`--fix` records null and fills it
  no more, until you delete that record).
- `chartwright explain <spec> [--chart NAME] [--json]`: per chart, each design-default
  field's value, whether it came from the spec, a fill or Superset, the value
  `design.filled` recorded, why, and how to change it. `advise --chart NAME` narrows a
  review, or a `--fix`, to one chart. MCP: `advise_spec` takes `chart`, and the new
  `explain_spec` tool returns the `explain --json` payload (eleven tools).

### Changed

- Each `fixed` entry from `advise --fix` and `fix_spec` gains `kind` (`"fill"`, `"repair"`,
  or `"release"` when a fill passes to the author) and a `why` line. `redesign` still
  applies repairs only and names `advise --fix` when fills are waiting. The brief and the
  skill tell an AI author to leave design defaults unset, run `--fix` before building, and
  edit the file it wrote. The design brain's version is now 5.
- `narrative.big-number-format` no longer fires on a COUNT KPI, where
  `default.count-format` offers the same fix.
- `y_axis_max` now applies on Superset 4.1.4 and 5.0.0 as well as 6.1.0, and on every
  chart with a value axis, not only lines. It was written into the ECharts Options,
  which only 6.1.0 reads, and is now the axis bound every release reads
  (`y_axis_bounds`). A dashboard built with the old form still decompiles to the same
  `y_axis_max`; the next apply rewrites it.

### Fixed

- Tables and pivots that hid their last rows passed `size.table-window`,
  `size.pivot-window`, `size.grid-fit` and the apply-time smoke warning. Their shared
  grid model was a guess. It is now measured on rendered Superset 4.1.4, 5.0.0 and 6.1.0
  (DESIGN-BRAIN §17), and counts what the old one missed:
  - a search box;
  - the page-size bar any `page_length` draws, and the pager's real height;
  - pivot header rows and the pinned totals row;
  - a horizontal scrollbar under a pivot with column dimensions.
  So these checks now warn where they used to pass. For example, a 5-row pivot by month
  at 8 units hid its last row, and a 10-row page at 12 units hides one on 6.1.0.
- A table's page size can now be set: `page_length`. The bundle carried
  `server_page_length: 10`, which Superset reads only with server pagination, so tables
  never paged by it; that key still ships, unchanged, and does nothing.
- The params contract's Mixed Chart entry listed only the keys the tool writes, so the
  drift check could not see a Mixed control missing from a release. It now lists every
  control the panel declares at 4.1.4, 5.0.0 and 6.1.0, and
  `tools/extract_mixed_contract.py` extracts it from the plugin source (`--check` says
  when the JSON no longer matches).

## 0.2.1 (2026-10-04)

### Fixed

- Charts over time (line, bar, area and scatter charts, trendline KPIs, and mixed charts
  with a time x axis) ignored both their own `time_range`, when set, and the dashboard's
  time filter. They showed every date while the filter bar counted them as filtered.
  - The next `apply` rewrites those charts, so both ranges work.
  - `plan` does not flag dashboards built with 0.2.0, so run `apply` on them even when
    `plan` is clean.
- `apply`'s data check queried every chart over all dates. It now uses each chart's own
  `time_range`, so a range that matches no rows shows up as an empty-chart warning.

## 0.2.0 (2026-10-03)

The design brain, plus the fixes found reviewing it.

### Added

- **A missing column names the real ones.** The error suggests up to three close matches
  (a case-only difference first, then by spelling) and lists the dataset's columns, up to
  50, where it used to give only their count. The JSON error carries the suggestions in a
  new `candidates` field, so an AI agent can correct the spec in one round; the MCP
  tools return the same.
- **`layout.footer`**: rows below the rows, tabs, or sketch, outside any tab,
  so a tabbed dashboard shows them under every tab. Round-trips through
  decompile and `plan`; rows below a dashboard's tabs, including ones placed
  in Superset's UI, decompile as its footer.
- **Markdown heights in fifths of a unit** (0.2 = 8 px, one Superset grid row),
  so a slim header or footer fits exactly (`"height": 1.6` is 64 px). Whole
  numbers are unchanged. Decompile reads text-block heights exactly instead of
  rounding them to whole units, so `plan` now reports a text block resized in
  the UI.
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
- Select filters take `default_to_first` (select the first value on load),
  `sort_descending` and `required`: with year labels like "2026 (this year)", a
  Year filter always opens on the latest year, with no literal default to go stale.
- Pivot totals: `row_totals` (a total per row, as a column at the right, e.g. the
  year beside its months) and `column_totals` (a total row at the bottom).
  `measure_totals`: with several metrics, a total after each metric's block of columns
  (Superset's column subtotals, the metric being the outer column level); `row_totals`
  adds the metrics together, e.g. orders plus revenue.
- `dashboard.label_colors`: a fixed colour per series label on every chart (the
  dashboard's custom label colours, which 6.1.0 applies over the scheme and the per-view
  map). Superset otherwise assigns colours as the page loads, so a measure can change
  colour between charts and visits. Validated as `#RRGGBB`, decompiled, and compared by
  `plan` (`label_colors_changed`).
- X-axis labels on timeseries, bar and mixed charts: `x_label_format` (a d3 time
  format, e.g. `%b` for "Sep"), `x_label_every` (a label at every month, week or
  category) and `x_label_rotation`. Superset's default writes full month names and
  January as the year, at a spacing it picks from the width, then drops labels that
  collide, so 13 months read "September, November, 2026, March" with gaps that
  differ chart to chart. `x_label_every` uses two 6.1.0 controls (the grain as the
  widest tick spacing on a time axis, interval 0 on a category axis); older releases
  ignore them, and the drift check allows exactly those keys before 6.1.0 (`SINCE`).
  On a time axis it also keeps both ends labelled: 6.1.0 left the first and last month
  of a 13-month line blank (a tick on the axis edge gets no label, and Superset's forced
  last label hid the last tick's), so the panel's ECharts Options (`echart_options`,
  6.1.0) pad a line's axis like a bar's and label the ticks only. `y_axis_max` on a line
  sets the top of the value axis (e.g. 1 for a share, where the default ran to 120 %).
- Mixed chart (`"type": "mixed"`, Superset's Mixed Chart): two queries, `a` and
  `b`, each drawn as bars or a line on the primary or secondary value axis, over
  a time column (`time_grain`) or any column (a categorical axis, e.g. by cause).
  Compiled, decompiled, smoke-queried (both queries) and in the params contract
  for all three releases.
- Sub-tabs: a tab can hold `tabs` (one level deep, each with rows or a sketch)
  instead of rows, compiled as Superset's tabs-inside-a-tab and decompiled back.
- Select filters take a `default` (values selected on load, e.g. a relative
  "This year" that stays right after 1 January) and `charts` (scope the filter to
  named charts, resolved to slice ids at apply, as on range filters).
- `number_format` on timeseries and categorical bar charts (the value axis;
  it was hard-coded to Superset's smart number, so a rate read `0.9760`, not `97.6%`).
- Tables take `cell_bars` (Superset draws bars behind numeric cells by default, including
  ids and years) and `date_format` (e.g. `%Y-%m-%d`, not a midnight timestamp); pivots
  take `number_format` (cells and totals, e.g. `,.0f` for `2,570`, not `2.57k`); a mixed
  chart's series take `markers` (a line over a single category draws nothing without one).

### Fixed

- `schema/dashboard_spec.schema.json` had not changed since 0.1.0 and was missing fields
  the spec has gained. It is regenerated from `chartwright schema`, and a test fails if
  the two drift apart again.
- `chartwright.__version__` said 0.1.0. It now matches the package version, enforced by a
  test.
- Corrected text that disagreed with the code: the skill and a code docstring said 14
  chart types (there are 15) and the skill left the numeric range filter out of the
  filter bar; `LAYOUT-GUIDE.md` said the design review skips the footer (most rules
  include it); `DESIGN-BRAIN.md` §9 listed widths as auto-fixable (they are report-only).
- The login error and `docs/VERIFICATION.md` said Superset disables its password login
  API on SSO or OAuth instances. Superset's source doesn't support that: the login API
  stays registered whatever the sign-in method. Both now say what is true either way:
  Chartwright signs in with a Superset account's own password (database or LDAP) or
  Preset API tokens, never through SSO or OAuth.
- A failed build now puts the backup back by the same rule whatever the error. A chart
  update that Superset rejected after the import left the dashboard half-updated, while a
  connection drop at the same step restored it; both now restore. An error that wasn't a
  Superset error (a bug in Chartwright) skipped the restore and lost the backup path;
  it now follows the same rule and returns the normal report. Failures after the chart
  updates (linkage, filter scopes, chart queries) still leave the new version live.
- Two applies to the same dashboard within one second wrote the same backup name, and
  the second overwrote the first. Backup names now go to the microsecond
  (`20261003T141502.123456.zip`) and are never overwritten.
- When an automatic restore failed, the suggested `chartwright restore <zip>` command
  left out the required `--profile`, so pasting it gave a usage error. It now includes it.
- `docs/VERIFICATION.md` said CI runs on every push, and that the live check covers a
  range filter. CI runs on pull requests, pushes to main and manual runs, and the range
  filter is covered by the second-writer and fault-injection runs.
- `pip install "chartwright[mcp]"` installed mcp 2, which renamed `mcp.server.fastmcp`,
  so `chartwright-mcp` failed to start. It now runs on mcp 1 and mcp 2 (the extra
  allows `mcp>=1.0,<3`), and CI tests both.
- `advise` (and `advise_spec`, `plan_dashboard`) crashed with a `KeyError` on any chart in
  `layout.footer`: the design checks now see the footer, and `layout.markdown-height`
  checks and fixes text blocks in the footer and in sub-tabs too.
- Decompile wrote a spec that failed validation when a table kept settings for a column
  it no longer queries (Superset keeps `column_config` entries and colour rules after
  the column leaves the query). Those settings are now dropped and listed as losses, on
  tables and pivots alike.
- Decompile wrote both `default_to_first` and `default` for a select filter that had
  "select first value" on and a saved value, which the spec rejects. It keeps
  `default_to_first` alone: Superset picks the first value again on load.
- `restore` (and the automatic restore after a failed apply) now moves each chart back
  onto its backed-up dataset, as apply does, so the chart's dataset matches its params.
- A mixed chart on a category column became a time axis when it had a `time_grain`,
  which every chart built in Superset's UI does. The dashboard time range then stopped
  reaching it and `x_label_every` sent the time-axis control. The column's reported type
  now decides; `time_grain` decides only when the type is unknown.
- `docs/FEATURES.md` said every filter-bar control can be scoped to specific
  charts. Value pickers (`select`) and numeric sliders (`range`) can; the time
  range applies to the whole dashboard.
- A select filter with `default_to_first` showed its first value while every chart queried
  unfiltered until the viewer pressed Apply. Superset applies a value on load only for a
  filter marked `requiredFirst`, which its own form saves with "select first value"
  (`FilterBar/index.tsx`, `FiltersConfigModal/utils.ts`); the compiler now marks it too.
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

- **The MCP tools return typed errors.** A tool that signs in (`check_spec`,
  `build_dashboard`, `plan_dashboard`, `advise_spec` with a profile,
  `decompile_dashboard`, `redesign_dashboard`) used to raise on a profile, password,
  sign-in or Superset error, which reached the agent as a bare tool failure. They now
  return the CLI's JSON: stage `profile` for profile and password problems, code `api`
  for sign-in and Superset errors, code `unexpected` for anything else.
- **`plan` reports missing references as data.** When the spec names something the
  instance doesn't have, `plan` is `blocked` and returns the same typed
  `resolution_errors` list as `check` and `apply`; `detail` is a short sentence. It
  used to put a Python-formatted list inside `detail`, which scripts couldn't parse.
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
