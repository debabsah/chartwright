# Superset versions and upgrades

After you upgrade Superset, rebuild each dashboard from the spec you already have. The same spec applies to Superset 4.1.4, 5.0.0 and 6.1.0. After an upgrade, `chartwright apply` writes every chart's settings again from the spec, then confirms each chart is on the dashboard and runs its query. This works for dashboards that have a spec. For a dashboard someone made in Superset's UI, generate its spec first (see [Dashboards made in the UI](#dashboards-made-in-the-ui)).

## Why one spec works on all three releases

Superset's backend doesn't define the settings a chart can have. Each chart type's frontend plugin defines them, and they change between releases: 6.1.0 renamed the big-number subtitle setting, for example. Chartwright deals with that as follows:

- It writes only chart settings that the plugins of all three releases declare. The list comes from each plugin's source at 4.1.4, 5.0.0 and 6.1.0 (`tools/contracts/params-contract.json`).
- CI compiles two example specs, one of which uses all 15 chart types, and fails if any setting they produce is missing from a release's list (`tools/params_drift.py`). Settings that only spec fields outside those two specs produce aren't checked in CI.
- There's one named exception: `x_label_every` and `y_axis_max` also write controls that only 6.1.0 has. Older releases ignore them.
- Each `apply` exports the dataset records from the instance it targets. The bundle it builds carries nothing that only 6.1.0 accepts, so a spec also applies to an older release.

The Superset source behind each of these points is cited in [CONTRACTS.md](CONTRACTS.md) ("What chart options mean").

## Rebuild your dashboards after an upgrade

1. **Before you upgrade, see what differs from each spec.** Run `chartwright plan orders.json --profile prod`. Exit 0 means the dashboard matches the spec. Exit 1 lists the charts and filters that were added, changed or removed (it also exits 1 when a reference is missing or sign-in fails), plus any title, layout, cross-filter or label-colour change. Edits made in the UI show up there too. The next `apply` overwrites them, so copy any edit you want to keep into the spec first. For chart heights dragged in the UI, `chartwright absorb orders.json --profile prod` does the copying.
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
| `check` | Confirms that every dataset, column and saved metric the spec names still exists on the upgraded instance, and lists every missing one at once |
| `apply` | Saves a backup of the live dashboard. Updates each chart in place, so chart ids and links stay the same, writing every setting from the spec. Confirms every chart is linked to the dashboard, sets filter scopes, and queries every chart: a query error fails the run, and a chart with no rows is reported as a warning |

When `apply` fails after the import step, the new version stays live. The report gives the backup's path, and `chartwright restore <backup.zip> --profile prod` puts the backup back.

## What CI builds on each release

On every pull request and every push to main, CI starts a real container of each release (4.1.4, 5.0.0 and 6.1.0) and runs these against it (`.github/workflows/ci.yml`, [VERIFICATION.md](VERIFICATION.md)):

- **One build of a 16-chart spec**: all 15 chart types, two tabs, a text block, WHERE filters on charts, two value pickers and a time range. It checks that every chart is linked and queries every chart, then applies the spec again to confirm chart ids don't change.
- **25 cycles of random edits**: charts added, removed and renamed, filters added and removed, layouts switched between rows and tabs. After each cycle, `plan` must come back clean.
- **Stale-tab overwrites and injected faults**: these use a numeric range filter scoped to named charts.

These spec fields have offline tests, but no live CI build uses them on any release:

- **Layout:** `sketch`, sub-tabs and `footer`.
- **Filters:** `default`, `default_to_first`, `required`, the time-range `default`, and `charts` on a value picker.
- **Colour:** `conditional_formatting`, `label_colors`, and `cross_filters: true`.
- **Charts:** table `hidden`, `number_formats`, `cell_bars` and `date_format`, the big-number `subtitle`, `y_axis_max`, and `x_label_rotation`.

## Differences between releases

| What you use | On 6.1.0 | On 4.1.4 and 5.0.0 |
|---|---|---|
| `x_label_every`, `y_axis_max` | A label at every step; the value axis stops at the number you set | No effect; Superset spaces the labels and sets the axis itself |
| Table `hidden` | The column is queried but not shown | The column is shown |
| Table colour rule with `apply_to` or `paint: "text"` | Paints another column, the whole row, or the text | These two settings aren't read, so the rule colours the tested value's own cell (from plugin source; not tested live) |
| Colour rule with `<`, `>` or `between` | A solid colour | The colour fades with distance from the threshold |
| `label_colors` | Applied over the colour scheme | Unverified |
| Big-number `subtitle` | Shown. Chartwright writes the setting's older name, which 6.1.0 still displays | Shown |
| `decompile` of a big number whose subtitle was typed in Superset's editor | The subtitle shows up in the losses as `params not preserved`; add `subtitle` to the chart in the spec | The subtitle is carried over |
| A chart someone added in the UI to a dashboard Chartwright manages | The next `apply` takes it off the dashboard, and it stays in Superset on no dashboard | The next `apply` fails at its linkage step and names the chart as unexpected. To fix it, take the chart off the dashboard in Superset's editor and add it to the spec |

A spec that uses 6.1.0-only fields still passes `chartwright validate`, and every setting it writes is declared by the table plugin of all three releases. This one does:

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
- **Specs:** a spec applies to 4.1.4 and 5.0.0 the same way. Chartwright builds the bundle from the spec and takes the dataset records from the target instance. Fields that only 6.1.0 reads have no effect there (see the table above).
- **Backups:** a backup saved on 6.1.0 is a 6.1.0 export, so `restore` can't put it onto 4.1.4 or 5.0.0. Apply the spec there instead.

## Dashboards made in the UI

A dashboard with no spec gets none of this. Generate its spec with `chartwright decompile <slug> --profile prod -o spec.json`, give the spec a new slug, and apply it. That builds a spec-managed copy beside the original. [CLONE-A-DASHBOARD.md](CLONE-A-DASHBOARD.md) gives the steps and lists the settings decompile drops.

## Limits

| What's true today | What to do instead |
|---|---|
| Tested on Superset 4.1.4, 5.0.0 and 6.1.0 only. On any other release, chart settings aren't checked against that release's plugins | Upgrade a test instance to that release and apply your specs there first. The linkage check and per-chart queries in the `apply` report show any chart that didn't land or doesn't return data |
| The CI build doesn't use every spec field (listed above) | Apply specs that use those fields on your test instance after the upgrade, and look at those charts before you upgrade production |
| `plan` names changed charts but not the setting that changed | Run `chartwright decompile <slug> --profile prod -o live.json` and diff `live.json` against your spec |
| `plan` compares only settings a spec can express, so changes to things like dashboard CSS don't show | Expect `apply` to reset them anyway, and keep styling in the spec (`label_colors`, colour rules, text blocks) |
| Every `apply` overwrites edits made in the UI | Run `plan` before the upgrade and copy the edits you want into the spec |
| A failure after the import step leaves the new version live | Run `chartwright restore` with the backup path from the report |

## Related

- [CLONE-A-DASHBOARD.md](CLONE-A-DASHBOARD.md): generate a spec from a dashboard made in the UI
- [HISTORY-AND-ROLLBACK.md](HISTORY-AND-ROLLBACK.md): backups, `restore` and spotting UI edits
- [LIMITS.md](LIMITS.md): every limit, with what to do instead
- [CONTRACTS.md](CONTRACTS.md): the Superset behaviour Chartwright relies on, cited to source at each release
- [VERIFICATION.md](VERIFICATION.md): how each release is tested
