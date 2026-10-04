# Superset Contracts

The tool's guarantees rest on how Apache Superset actually behaves: what its
importer accepts, how it stores dashboard settings, and what its chart
plugins expect. This page states each behavior the tool depends on, cites it
to Superset's source at the three verified releases (4.1.4, 5.0.0, 6.1.0),
and says what the tool does about it.

Reading a citation: `6.1.0 superset/commands/dashboard/importers/v1/__init__.py:138`
names the release tag, the file, and the line. Release tags are immutable,
so any citation can be checked against a fresh checkout of that tag.
`docs/VERIFICATION.md` is the companion page: it covers what is proven by
running the tool against real instances of all three releases.

## When a bundle is imported

- **Charts whose dataset is not in the bundle are skipped silently, and the
  import still returns HTTP 200.** The importer creates a chart only if the
  chart's dataset, and that dataset's database, are present as YAML in the
  same bundle, even when the dataset already exists on the target
  (4.1.4/5.0.0 `superset/commands/dashboard/importers/v1/__init__.py:110`,
  6.1.0 `:138`; success response 6.1.0 `superset/dashboards/api.py:1953`).
  The tool bundles the target's own dataset and database records with every
  apply, and verifies chart linkage afterward instead of trusting the
  status code.
- **Import never overwrites an existing chart, dataset, or database.** The
  importer hard-codes overwrite off for everything except the dashboard
  itself (chart 4.1.4/5.0.0 `.../dashboard/importers/v1/__init__.py:119`,
  6.1.0 `:147`; existing objects returned untouched in
  `superset/commands/chart/importers/v1/utils.py:63`, all releases). The
  tool updates the charts it owns through the chart API instead, in place,
  keeping their ids stable.
- **Every chart node in the layout must carry an integer id, or the import
  fails with a 500** (`superset/commands/dashboard/importers/v1/utils.py:48`,
  byte-identical in all three releases). The compiler emits valid
  placeholder ids in every bundle.
- **On 4.1.4 and 5.0.0, one unresolvable chart reference can silently drop
  other charts' links, and the import still returns 200** (4.1.4/5.0.0
  `.../v1/__init__.py:136-138`; fixed in 6.1.0 `:196-198`). The tool
  resolves every reference before importing and verifies every link after,
  so this cannot pass unnoticed.
- **Removing a chart behaves differently by release.** A 6.1.0 import
  relinks the dashboard from the bundle alone; 4.1.4 and 5.0.0 merge,
  keeping links to charts the bundle no longer contains (6.1.0
  `.../v1/__init__.py:186-193`). The tool deletes its own charts when they
  leave the spec, so the result is the same on every release.
- **The dashboard's CSS travels in the bundle, on every release.** The
  import schema accepts `css` (`ImportV1DashboardSchema`, 4.1.4
  `superset/dashboards/schemas.py:461`, 5.0.0 `:477`, 6.1.0 `:508`), the
  importer writes it with the other dashboard fields (4.1.4/5.0.0
  `superset/commands/dashboard/importers/v1/utils.py:190`, 6.1.0 `:240`),
  and the export carries it back (`superset/models/dashboard.py:174` at
  4.1.4/5.0.0, `:187` at 6.1.0). An apply therefore replaces CSS edited in
  the UI with the spec's `css`, or clears it when the spec has none; `plan`
  reports the difference first.
- **Tags import on 6.0.0 or later, and only with tagging turned on.** The
  dashboard and chart import schemas gain `tags` at 6.0.0
  (`superset/dashboards/schemas.py:502`, `superset/charts/schemas.py:1589`;
  6.1.0 `:519`, `:1627`). 4.1.4 and 5.0.0 load every bundle file through
  schemas that reject a field they don't declare
  (`superset/commands/importers/v1/utils.py:187`), so a bundle carrying tags
  fails to import there. From 6.0.0 the importer applies tags only with the
  `TAGGING_SYSTEM` feature flag
  (`superset/commands/dashboard/importers/v1/__init__.py`, 6.0.0
  `:152,188`, 6.1.0 `:152,207`) and replaces the object's tags with the
  bundle's list (`superset/commands/importers/v1/utils.py`, 6.0.0 `:315-318`,
  6.1.0 `:335`). The tool writes `tags` only when the spec sets them, and
  `check`, `apply` and `plan` refuse a spec with `tags` on an instance older
  than 6.0.0; see "Which release is on the other end".
- **A chart's own settings change through the chart API.** Since the
  importer never overwrites an existing chart, a re-apply sends a chart's
  description, certification and cache timeout with its options in the
  in-place update; the chart update schema takes each one, empty included,
  in every release (4.1.4 `superset/charts/schemas.py:238,259,273,276`).
- **An import is a single database transaction** (6.1.0
  `superset/commands/importers/v1/__init__.py:85`): a failed import leaves
  no partial dashboard behind.

## How dashboard settings are stored

- **The last writer wins, in every release.** The dashboard update API has
  no version check, no ETag, and no conflict error; it re-serializes
  whatever the caller sends (4.1.4 `superset/dashboards/api.py:613`,
  `superset/commands/dashboard/update.py:59-64`,
  `superset/daos/dashboard.py:178,266`; same chain in 5.0.0 and 6.1.0).
  Two writers silently overwrite each other. This is why `chartwright plan` detects
  a clobbered dashboard, `chartwright apply` repairs it without changing chart ids,
  and every apply saves a backup first.
- **On 4.1.x, closing a browser tab writes that tab's whole in-memory copy
  of the dashboard back to the server**, including its filter configuration
  (4.1.4 `superset-frontend/src/dashboard/components/DashboardBuilder/DashboardContainer.tsx:121-125,213,224`).
  From 5.0.0 the same hook writes only color settings. A stale tab left
  open on an older release is therefore a real overwrite source, and it is
  the exact scenario the tool's stale-tab protection is tested against.
- **A dashboard setting a release doesn't know blocks later saves.** An
  import stores the dashboard's settings as given, but every later update
  checks them against the settings schema, which rejects keys it doesn't
  declare (4.1.4 `superset/dashboards/schemas.py:107-116,403-406`; the
  same in 5.0.0). `show_chart_timestamps` is declared from 6.1.0 (`:167`),
  so on 4.1.4 and 5.0.0 a dashboard carrying it imports, then fails every
  save, including apply's filter-scope step (6.0.0 does not declare it
  either). The compiler writes it only when the spec turns it on, and
  `check`, `apply` and `plan` refuse it on an instance older than 6.1.0.
- **The horizontal filter bar is behind a flag before 6.1.0.**
  `filter_bar_orientation` is a declared setting in every release, but
  4.1.4 and 5.0.0 draw the horizontal bar only with the
  `HORIZONTAL_FILTER_BAR` feature flag, off by default (4.1.4
  `superset/config.py:532`, 5.0.0 `:529`;
  `src/dashboard/components/DashboardBuilder/DashboardBuilder.tsx:399-403`
  at both); 6.1.0 reads the setting with no flag (`:381-382`).
- **A select filter's sort metric moved in 6.1.0.** 4.1.4 and 5.0.0 save it
  at the filter's top level (`FiltersConfigForm.tsx:1098` and `:1123`),
  6.1.0 inside its control values (`:1262-1265`); every release merges both
  into the filter's query (`src/dashboard/components/nativeFilters/utils.ts`,
  4.1.4 `:80`, 5.0.0 `:81`, 6.1.0 `:83`). The tool writes both.
- **Headers and dividers sit beside rows, never inside one.** The layout
  allows a HEADER or DIVIDER under the grid, a tab or a column, and a row
  holds only charts, markdown and columns (`src/dashboard/util/isValidChild.ts:64-104`,
  all three releases). The spec therefore places a header or divider as a
  row of its own.
- **The server normalizes what it stores and accepts dangling references.**
  Omitted settings are filled with defaults on write (4.1.4
  `superset/daos/dashboard.py:258-265`), and filter scopes pointing at
  deleted charts are stored without complaint in every release. The tool
  compares meaning rather than raw text when verifying, and `plan`
  recomputes filter scopes instead of trusting stored ids.

## Which release is on the other end

- **6.1.0 reports its version at `/version`; 4.1.4 and 5.0.0 report it in
  their sign-in page.** 6.1.0's health blueprint answers `GET /version`
  with JSON carrying `version_string`, signed out
  (`superset/views/health.py:36-45`, registered at
  `superset/initialization/__init__.py:234-236`). 4.1.4 and 5.0.0 have no
  such route (their `superset/views/health.py:22-29` serves `/health`,
  `/healthcheck` and `/ping` only), but their sign-in page carries the
  version in its bootstrap data: Flask-AppBuilder's `login_db.html` extends
  `appbuilder/base.html`, whose base template is `superset/base.html`
  (`superset/initialization/__init__.py:564` at 4.1.4, `:559` at 5.0.0),
  which extends `appbuilder/baselayout.html`, whose `data-bootstrap`
  attribute (`superset/templates/appbuilder/baselayout.html:45`) holds
  `common.menu_data.navbar_right.version_string`
  (`superset/views/base.py:282` at 4.1.4, `:274` at 5.0.0). `check`,
  `apply` and `plan` ask `/version` first, then the sign-in page, both
  signed out, and only when the spec uses one of the fields below. A
  development build reports 0.0.0, which counts as unknown.
- **A field a release can't take is refused before anything is written.**
  `chartwright/versions.py` lists each version-gated field with the first
  release that takes it: `tags` (6.0.0) and `show_chart_timestamps`
  (6.1.0), for the reasons above. Against an older instance, the spec gets
  a `superset_version_too_old` error at the resolve stage, so `apply` stops
  before its backup, import or any update; remove the field for that
  instance. When the instance doesn't report its version, the error is
  `superset_version_unknown`: state the release with `--superset-version`
  (the MCP tools' `superset_version`), which also skips the lookup.
  `compile --superset-version` runs the same check offline; without it,
  `compile` writes the same bundle for every release.
- **A field a release ignores is a warning.** A trendline's `subtitle` and
  a table's `column_headers` write controls that 6.0.0 added and older
  plugins never read (next section). `x_label_every` warns before 6.1.0: a
  time axis needs `force_max_interval`, a 6.1.0 control, while a mixed
  chart's category axis needs only `xAxisLabelInterval`, which 6.0.0 reads.
  A stacked mixed query with `show_value` labels every segment before
  6.0.0, because the Mixed Chart reads `onlyTotal` from 6.0.0 on
  (`MixedTimeseries/transformProps.ts`, 6.0.0 `:178-179`, 6.1.0
  `:186-187`). That warning names `show_value`, the field the spec wrote,
  since `only_total` is on by default; `only_total: false` asks for every
  segment on every release, so it raises nothing. These come back in
  `version_warnings`, and the dashboard still builds.
- **6.0.x is checked against its source, not tested live.** Each field's
  first release, 6.0.0 or 6.1.0, was read from the 6.0.0 tag, so `check`,
  `apply` and `plan` hold a 6.0.x instance to what that release takes. 6.0.x
  is not in the tested matrix, which stays 4.1.4, 5.0.0 and 6.1.0
  (`docs/VERIFICATION.md`).

## What chart options mean

- **Superset's backend has no schema for chart options.** Each chart type's
  options are defined only by its frontend plugin. The tool keeps a
  per-release contract extracted from those plugins
  (`tools/contracts/params-contract.json`), and CI fails if the tool ever
  emits an option a supported release does not declare
  (`tools/params_drift.py`).
- **The `mixed_timeseries` entry is extracted from the plugin source** by
  `tools/extract_mixed_contract.py`: `plugin-chart-echarts/src/MixedTimeseries/controlPanel.tsx`
  at 4.1.4, 5.0.0 and 6.1.0, with the sections and controls it pulls in
  (title, legend, tooltip, annotations, advanced analytics; query B's query
  keys take `_b`, its display keys `B`). Like the other entries it lists
  every control the panel declares, so the drift check sees the Mixed
  controls the tool does not emit yet; `--check` fails when the JSON no
  longer matches the source. Among the controls 6.1.0 declares and 5.0.0
  does not are `only_total` and `only_totalB`, both new in 6.0.0
  (`MixedTimeseries/controlPanel.tsx:204` at 6.0.0).
- **Formula annotation layers draw the same way in every release.** The line,
  bar, area, scatter and mixed panels all include the annotation section
  (`chart-controls/src/sections/annotationsAndLayers.tsx:31`), and both
  plugins draw a FORMULA layer with `transformFormulaAnnotation`
  (`plugin-chart-echarts/src/Timeseries/transformers.ts`, 4.1.4 `:356`,
  5.0.0 `:362`, 6.1.0 `:457`, the same body in each). Its colour, opacity,
  width and line style (solid, dashed or dotted) reach the chart; a formula
  layer has no on-chart label, so the tool offers none.
- **Options genuinely differ by release.** 6.0.0 renamed the big-number
  subtitle field (`subheader` became `subtitle`) and removed sort controls
  that older releases still have. The tool emits only options valid on all
  three releases, with named exceptions. An opt-in field documented with a
  later release may emit that release's controls: `x_label_every`
  (`force_max_interval`, new in 6.1.0, and `xAxisLabelInterval`, new in
  6.0.0: `controls.tsx:305` at 6.0.0; `Timeseries/Regular/*/controlPanel.tsx`
  and `MixedTimeseries/controlPanel.tsx`), a trend's `subtitle`
  (`BigNumberWithTrendline/controlPanel.tsx:33` at 6.0.0), a mixed query's
  `only_total` (`only_total`, `only_totalB`, 6.0.0), and a table's
  `column_headers` (`customColumnName` inside `column_config`, read at 6.0.0
  `plugin-chart-table/src/TableChart.tsx:806`, `:859` at 6.1.0). An older
  plugin never reads them, so an older Superset ignores them;
  `tools/params_drift.py` lists them in `SINCE`. The reverse also happens: a
  bar's category sort with several series writes `x_axis_sort_series` for
  4.1.4 and 5.0.0, which 6.0.0 and later no longer read (`UNTIL`). Every
  other key is held to every release.
- **A dashboard fills each missing chart option with the panel's default**
  before drawing (`applyDefaultFormData`, 4.1.4/5.0.0
  `src/dashboard/actions/hydrate.js:111`, 6.1.0
  `src/dashboard/actions/hydrate.ts:168`). The panel
  default is therefore what a chart shows when the spec omits a field, so the
  spec's defaults match it: `only_total` and a heatmap's `show_percentage`
  default to true, and the tool writes them only as false. Axis titles get an
  explicit margin, because 6.1.0's default x-title margin is 0, which draws
  the title over the tick labels.
- **A written Superset default draws the same chart as an omitted field.**
  Writing Superset's own value (`legend_position: "top"`, `legend_type:
  "scroll"`, a pie's `label_type: "key_percent"`, a funnel's `"key"`, a
  treemap's `"key_value"`, `marker_size: 6`, `opacity: 0.2`, the teal
  `trend_color` `#007A87`, a heatmap's `superset_seq_1`, or a dashboard's
  `refresh_frequency: 0` and `filter_bar_orientation: "vertical"`) changes
  nothing Superset draws. The spec keeps the value as written, so an author's
  choice survives validation; `compile` builds the same bundle as for the
  omitted field, and `plan` reads the two as equal. A decompiled spec leaves
  Superset's defaults out, since the stored chart can't say which one the
  author wrote. The list is `SUPERSET_DEFAULTS` in `chartwright/spec.py`.
  The design brain's fills (`advise --fix`, DESIGN-BRAIN.md §16) read the
  spec as written, so a written default is the author's choice and no fill
  replaces it; once decompiled, it is gone and the field reads as unset.
- **The value-axis bounds are the axis edges on every release.** The panel
  says the bounds only widen the axis, but the Timeseries and Mixed plugins
  hand `y_axis_bounds` to ECharts as the axis minimum and maximum
  (`Timeseries/transformProps.ts` 4.1.4 `:427,510`, 5.0.0 `:426,510`, 6.1.0
  `:677,951`; `MixedTimeseries/transformProps.ts` 4.1.4 `:361,534`, 6.1.0
  `:421,728`), so `y_axis_min` and `y_axis_max` set where the axis ends. The
  panel shows the bounds only while Truncate Y Axis is on; the tool writes
  them without turning truncation on, so the side without a bound still
  includes zero unless the spec sets `y_axis_truncate`.
- **A table's page size is `page_length`.** Superset reads
  `server_page_length` only with server pagination on, and `page_length`
  otherwise (`plugin-chart-table/src/transformProps.ts`, 4.1.4 `:631`, 5.0.0
  `:699`, 6.1.0 `:789`). The tool never turns server pagination on; the
  `server_page_length` it has always written is Superset's stored default and
  changes nothing. `page_length` in the spec sets the page.
- **A bar sorts its categories two ways.** With one series a post-processing
  sort orders the returned rows on the x column (`operators/sortOperator.ts`,
  which skips any chart with a groupby, 4.1.4 and 5.0.0 `:46`, 6.1.0 `:45`);
  with several series the plugin sorts the x values by name, reading
  `x_axis_sort` from 6.0.0 (`Timeseries/transformProps.ts`, 6.0.0 `:254`,
  6.1.0 `:335`) and
  `x_axis_sort_series` at 4.1.4 and 5.0.0 (`:243` at 4.1.4, `:246` at 5.0.0).
  `category_sort` writes whichever applies.
- **Which rows a bar's row limit keeps is set by the query's ORDER BY, not by
  that sort.** The bar's query takes its ORDER BY from `normalizeOrderBy`
  (`Timeseries/buildQuery.ts:93` at all three releases): the "Sort query by"
  control (`timeseries_limit_metric`, labelled "Sort by" at 4.1.4) if set,
  else the first metric (`query/normalizeOrderBy.ts` `:54` and `:74`, 6.1.0
  `:73`), descending
  unless `order_desc` is false (`query/buildQueryObject.ts`, 4.1.4 and 5.0.0
  `:131`, 6.1.0 `:133`). The control's own help text says so: it decides
  "what data are truncated" when a row limit is reached
  (`shared-controls/dndControls.tsx`, 5.0.0 `:195`, 6.1.0 `:229`; 4.1.4
  `:194` words it as "row selection criteria"). Ordered by value, a row limit
  below the number of categories kept the largest bars, which were then
  drawn in category order with gaps. So `category_sort` also sets "Sort
  query by" to `MIN(<x column>)` and `order_desc` to the sort's direction:
  grouped by the x column, `MIN` of it is the category itself, so the row
  limit keeps the first categories in reading order. Superset compiles an
  ad-hoc metric in the ORDER BY (`superset/models/helpers.py`, 4.1.4 `:1570`,
  5.0.0 `:1581`, 6.1.0 `:2796`), and the metric is not drawn as a series
  because its label is not `x_axis_sort`
  (`operators/utils/extractExtraMetrics.ts:35`, all three releases).
  With `series_limit` the same control ranks the series, so the tool leaves
  it to the series limit; there a `row_limit` still keeps the largest values,
  and `advise` warns about it (`data.top-n-sort`).
- **On 4.1.4, heatmap and histogram exist twice** (a legacy plugin and a
  current one, with different options). The tool builds the current ones;
  decompiling a dashboard built on the legacy ones reports them as named
  losses rather than guessing.

## Differences the tool absorbs

- Chart and dashboard list APIs cannot filter by uuid before 6.1.0 (4.1.4
  `superset/charts/api.py:219`). The tool looks up by name and matches the
  uuid client-side, which works on every release.
- The dashboard detail API omits the uuid before 6.1.0 (present from 6.1.0,
  `superset/dashboards/schemas.py:245`). The tool reads it from the list
  API, which includes it on every release (4.1.4
  `superset/dashboards/api.py:221`).
- **6.0.0 renamed the big-number subtitle controls.** 4.1.4 and 5.0.0
  declare `subheader` and `subheader_font_size`
  (`plugin-chart-echarts/src/BigNumber/sharedControls.ts` at both tags);
  6.0.0 and 6.1.0 replace them with `subtitle` and `subtitle_font_size` but
  still render the legacy pair through a fallback
  (`BigNumber/BigNumberTotal/transformProps.ts`, 6.0.0 `:69-72`, 6.1.0
  `:77`). The tool
  emits the legacy pair with pinned sizes, which every release honors.
  Left unpinned, that fallback sizes the subtitle at the full card height,
  so short subtitles render huge and cropped.
- **A time range reaches a chart only through the chart's time binding.**
  This holds for a chart's own `time_range` and for a dashboard time
  filter alike. Without a binding the chart ignores the range while the
  filter bar still counts it as filtered. The backend applies a query's
  `time_range` only through `granularity` (`get_sqla_query`,
  `superset/models/helpers.py:1673` at 4.1.4, `:1684` at 5.0.0, `:2926`
  at 6.1.0).
  - A chart without a time axis binds the dataset's main time column as
    `granularity_sqla`. This was verified live on 6.1.0: before the
    binding, a one-week filter left a full-month total on screen.
  - A chart drawn on a time axis (line, bar, area, scatter, trendline KPI,
    and a mixed chart over time) gets the filter Superset's own charts
    carry: an adhoc `TEMPORAL_RANGE` filter on the axis column, with the
    chart's `time_range` as its value. A granularity there would replace
    the axis column (`_apply_granularity`,
    `superset/common/query_context_factory.py:115` at 4.1.4 and 5.0.0,
    `:235` at 6.1.0). At query time `_apply_filters` (`:189`, `:311` at
    6.1.0) overwrites the filter's value with the query's `time_range`
    whenever that is set, "No filter" included. The tool therefore writes
    the same value in both places, and a dashboard time filter replaces
    it. Verified live on 4.1.4, 5.0.0 and 6.1.0: without that filter,
    these charts ignored both ranges.
  - A time filter default is written to both halves of `defaultDataMask`
    (`src/filters/components/Time/TimeFilterPlugin.tsx:95` at 6.1.0:
    queries read `extraFormData.time_range`, the pill reads
    `filterState.value`).
- **A 6.1.0 export does not import on older releases.** 6.1.0 bundles carry
  fields older importers reject as unknown, such as the dashboard's theme
  reference (6.1.0 `superset/commands/dashboard/export.py:165`) and newer
  dataset fields. Moving dashboards from an older Superset to a newer one
  works as-is; newest-to-oldest is limited by Superset's own import
  schemas. Applying a spec to the instance it names is unaffected: the tool
  always round-trips dataset records from the target itself, in the
  target's own dialect.

## Checking these facts

Every citation names an immutable release tag: clone the tag shallowly and
open the file at the line. The executable checks live in `tools/` and run
in CI on every push; what execution proves, and how to reproduce it, is
`docs/VERIFICATION.md`.
