# Superset versions and upgrades

After you upgrade Superset, rebuild each dashboard from the spec you already have. The same spec applies to Superset 4.1.4, 5.0.0 and 6.1.0. After an upgrade, `chartwright apply` writes every chart's settings again from the spec, then confirms each chart is on the dashboard and runs its query. This works for dashboards that have a spec. For a dashboard someone made in Superset's UI, adopt it first (see [Dashboards made in the UI](#dashboards-made-in-the-ui)).

## Why one spec works on all three releases

Superset's backend doesn't define the settings a chart can have. Each chart type's frontend plugin defines them, and they change between releases: 6.1.0 renamed the big-number subtitle setting, for example. Chartwright deals with that as follows:

- It writes the chart settings each release's plugins declare. The lists come from each plugin's source at 4.1.4, 5.0.0 and 6.1.0 (`tools/contracts/params-contract.json`). A setting only a newer release has is written only where an older release ignores it, and each such case is a named exception in the check below.
- CI compiles four example specs, which between them use all 15 chart types and the display and dashboard settings, and fails if any setting they produce is missing from a release's list without a named exception (`tools/params_drift.py`).
- `check`, `apply` and `plan` ask the instance for its release, then:
    - refuse, before writing anything, a field it can't take: `tags` and `theme` before 6.0, `show_chart_timestamps` before 6.1;
    - warn about a field it ignores: `x_label_every` before 6.1; a trendline's `subtitle`, a table's `column_headers` and `show_value` on a stacked mixed chart before 6.0.
- `--superset-version` states the release when the instance doesn't report it, and `chartwright compile --superset-version 5.0.0` runs the same check offline.
- Each `apply` exports the dataset records from the instance it targets, so the bundle matches that instance.

The Superset source behind each of these points is cited in [CONTRACTS.md](CONTRACTS.md) ("What chart options mean").

## Rebuild your dashboards after an upgrade

1. **Before you upgrade, see what differs from each spec.** Run `chartwright plan orders.json --profile prod`. Exit 0 means the dashboard matches the spec. Exit 1 lists the charts and filters that were added, changed or removed (it also exits 1 when a reference is missing or sign-in fails), plus any title, layout, cross-filter, label-colour, CSS or dashboard-setting change. Edits made in the UI show up there too. The next `apply` overwrites them, so copy any edit you want to keep into the spec first. For chart heights dragged in the UI, `chartwright absorb orders.json --profile prod` does the copying.
2. **Try the new release on a test instance first.** Upgrade a test instance, then apply your specs there under the test instance's own profile.
3. **Upgrade production** with Superset's own upgrade steps.
4. **Check, then apply each spec:**

```bash
# Before the upgrade: see what differs from each spec, including UI edits.
for f in specs/*.json; do
  chartwright plan "$f" --profile prod
done

# After the upgrade: check each spec's references, then rebuild it.
for f in specs/*.json; do
  chartwright check "$f" --profile prod && chartwright apply "$f" --profile prod
done
```

| Step | What Chartwright checks or does |
|---|---|
| `plan` | Compares the live dashboard with the spec without changing anything. It names each changed chart, not the setting that changed, and it sees only settings a spec can express |
| `check` | Confirms that every dataset, column and saved metric the spec names, and the owners and theme it names, still exist on the upgraded instance, and that the release can take every field; lists every problem at once. Columns inside custom SQL, which it can't look up, are listed under `unchecked_sql` |
| `apply` | Saves a backup of the live dashboard. Updates each chart in place, so chart ids and links stay the same, writing every setting from the spec. Confirms every chart is linked to the dashboard, sets filter scopes, and queries every chart: a query error fails the run, and a chart with no rows is reported as a warning |

When `apply` fails after the import and the chart updates, the new version stays live. The report gives the backup's path, and `chartwright restore <backup.zip> --profile prod` puts the backup back.

## What CI builds on each release

On every pull request and every push to main, CI starts a real container of each release (4.1.4, 5.0.0 and 6.1.0) and runs these against it (`.github/workflows/ci.yml`, [VERIFICATION.md](VERIFICATION.md)):

- **Builds of three specs**: a 16-chart spec with all 15 chart types, two tabs, a text block, WHERE filters on charts, two value pickers and a time range; a spec that sets the charts' display options; and one that sets the dashboard's settings, a header and a footer. Each checks that every chart is linked and queries every chart, then applies the spec again to confirm chart ids don't change.
- **Other live checks**: a chart added in the UI taken off on the next apply, with restores matching their backups; a dashboard adopted in place; standards content written and read back; and, when the browser for them installs, locked text checked on the rendered page and each chart's query saved for reports.
- **25 cycles of random edits**: charts added, removed and renamed, filters added and removed, layouts switched between rows and tabs. After each cycle, `plan` must come back clean.
- **Stale-tab overwrites and injected faults**: these use a numeric range filter scoped to named charts.

These spec fields have offline tests, but no live CI build uses them on any release:

- **Layout:** `sketch` and sub-tabs.
- **Filters:** a value picker's `default` and `default_to_first`.
- **Colour:** `conditional_formatting`, `label_colors`, and `cross_filters: true`.
- **Charts:** table `hidden`, `cell_bars` and `date_format`, and the big-number total's `subtitle`.

## Differences between releases

| What you use | On 6.1.0 | On 4.1.4 and 5.0.0 |
|---|---|---|
| `tags`, `theme` | Applied | Refused before anything is written (both need 6.0 or later) |
| `show_chart_timestamps` | Applied | Refused before anything is written |
| `x_label_every` | A label at every step | No effect, with a warning; Superset spaces the labels itself |
| A trendline's `subtitle`, a table's `column_headers` | Shown | No effect, with a warning |
| `show_value` on a stacked query of a mixed chart | Labels each stack's total | Labels every segment, with a warning; set `only_total: false` for the same labels on every release |
| `filter_bar_orientation: "horizontal"` | A filter bar across the top | Needs Superset's `HORIZONTAL_FILTER_BAR` feature flag |
| `owners` given as usernames | Found by username | Found by username only with Superset's `FAB_ADD_SECURITY_API` setting on; emails work on every release |
| Table `hidden` | The column is queried but not shown | The column is shown |
| Table colour rule with `apply_to` or `paint: "text"` | Paints another column, the whole row, or the text | These two settings aren't read, so the rule colours the tested value's own cell (from plugin source; not tested live) |
| Colour rule with `<`, `>` or `between` | A solid colour | The colour fades with distance from the threshold |
| `label_colors` | Applied over the colour scheme | Unverified |
| Big-number `subtitle` | Shown. Chartwright writes the setting's older name, which 6.1.0 still displays | Shown |

A spec can use settings only 6.1.0 reads and still apply to every release, because each setting it writes is declared by the table plugin of all three. This one does:

```json
{
  "spec_version": "1",
  "dashboard": {"title": "Order status", "slug": "ops-order-status"},
  "charts": [
    {"name": "Orders by region", "type": "table",
     "metrics": ["SUM(amount) AS Revenue"], "groupby": ["region", "status"],
     "hidden": ["status"],
     "conditional_formatting": [
       {"metric": "Revenue", "operator": "<", "target": 1000, "color": "red", "apply_to": "row"}
     ],
     "dataset": {"database": "warehouse", "table": "orders"}}
  ],
  "layout": {"rows": [["Orders by region"]]}
}
```

On 6.1.0, a row with revenue under 1,000 turns red and the status column is hidden. On 4.1.4 and 5.0.0, the status column is shown and the rule colours only the Revenue cell, fading with distance from the threshold.

## Moving from a newer Superset to an older one

- **Superset exports:** a dashboard export from Superset 6.1.0 carries a `theme_uuid` field (`superset/commands/dashboard/export.py:165` at 6.1.0). The 4.1.4 and 5.0.0 import schemas don't declare that field, so their importers reject the export. Exports from an older release import into a newer one as they are ([CONTRACTS.md](CONTRACTS.md), "A 6.1.0 export does not import on older releases").
- **Specs:** a spec applies to 4.1.4 and 5.0.0 the same way. Chartwright builds the bundle from the spec and takes the dataset records from the target instance. A field the older release can't take, such as `tags` or `theme`, is refused before anything is written, and one it ignores comes back as a warning (see the table above).
- **Backups:** a backup saved on 6.1.0 is a 6.1.0 export, so `restore` can't put it onto 4.1.4 or 5.0.0, and it refuses a backup from another instance anyway unless you pass `--to-other-instance`. Apply the spec there instead.

## Dashboards made in the UI

A dashboard with no spec gets none of this. Adopt it with `chartwright adopt <slug> --profile prod -o spec.json`: the spec updates that same dashboard, and adopt lists what its first apply resets ([MOVE-BETWEEN-INSTANCES.md](MOVE-BETWEEN-INSTANCES.md#take-over-a-dashboard-made-in-the-ui)). To build a copy beside the original instead, see [CLONE-A-DASHBOARD.md](CLONE-A-DASHBOARD.md).

## Limits

| What's true today | What to do instead |
|---|---|
| Tested on Superset 4.1.4, 5.0.0 and 6.1.0 only. On any other release, chart settings aren't checked against that release's plugins | Upgrade a test instance to that release and apply your specs there first. The linkage check and per-chart queries in the `apply` report show any chart that didn't land or doesn't return data |
| The CI build doesn't use every spec field (listed above) | Apply specs that use those fields on your test instance after the upgrade, and look at those charts before you upgrade production |
| `plan` names changed charts but not the setting that changed (on an adopted spec it lists them under `chart_option_changes`) | Run `chartwright decompile <slug> --profile prod -o live.json` and diff `live.json` against your spec |
| `plan` compares only settings a spec can express | Expect `apply` to reset the rest, and keep styling in the spec (`css`, `label_colors`, colour rules, text blocks) |
| Every `apply` overwrites edits made in the UI | Run `plan` before the upgrade and copy the edits you want into the spec |
| A failure after the import and the chart updates leaves the new version live | Run `chartwright restore` with the backup path from the report |

## Related

- [CLONE-A-DASHBOARD.md](CLONE-A-DASHBOARD.md): copy a dashboard, including one made in the UI
- [HISTORY-AND-ROLLBACK.md](HISTORY-AND-ROLLBACK.md): backups, `restore` and spotting UI edits
- [LIMITS.md](LIMITS.md): every limit, with what to do instead
- [CONTRACTS.md](CONTRACTS.md): the Superset behaviour Chartwright relies on, cited to source at each release
- [VERIFICATION.md](VERIFICATION.md): how each release is tested
