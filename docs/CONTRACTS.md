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
- **The server normalizes what it stores and accepts dangling references.**
  Omitted settings are filled with defaults on write (4.1.4
  `superset/daos/dashboard.py:258-265`), and filter scopes pointing at
  deleted charts are stored without complaint in every release. The tool
  compares meaning rather than raw text when verifying, and `plan`
  recomputes filter scopes instead of trusting stored ids.

## What chart options mean

- **Superset's backend has no schema for chart options.** Each chart type's
  options are defined only by its frontend plugin. The tool keeps a
  per-release contract extracted from those plugins
  (`tools/contracts/params-contract.json`), and CI fails if the tool ever
  emits an option a supported release does not declare
  (`tools/params_drift.py`).
- **Options genuinely differ by release.** 6.1.0 renamed the big-number
  subtitle field (`subheader` became `subtitle`) and removed sort controls
  that older releases still have. The tool emits only options valid on all
  three releases.
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
- **6.1.0 renamed the big-number subtitle controls.** 4.1.4 and 5.0.0
  declare `subheader` and `subheader_font_size`
  (`plugin-chart-echarts/src/BigNumber/sharedControls.ts` at both tags);
  6.1.0 replaced them with `subtitle` and `subtitle_font_size` but still
  renders the legacy pair through a fallback
  (`BigNumber/BigNumberTotal/transformProps.ts:77` at 6.1.0). The tool
  emits the legacy pair with pinned sizes, which every release honors.
  Left unpinned, that fallback sizes the subtitle at the full card height,
  so short subtitles render huge and cropped.
- **A dashboard time filter reaches a chart only through the chart's time
  binding.** A chart with no time column of its own ignores the filter
  while the filter bar still counts the chart as filtered (verified live
  on 6.1.0: a one-week filter left a full-month total on screen). The
  tool binds every chart that has no time axis to the dataset's main time
  column, and a time filter default is written to both halves of
  `defaultDataMask` (`src/filters/components/Time/TimeFilterPlugin.tsx:95`
  at 6.1.0: queries read `extraFormData.time_range`, the pill reads
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
