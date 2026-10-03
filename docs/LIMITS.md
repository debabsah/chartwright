# Limits

Each limit below comes with what to do instead, so you can tell whether Chartwright fits your Superset setup before you point it at an instance you care about. It starts with how Chartwright decides which dashboards and charts it may change, because several of the limits follow from that.

## How Chartwright decides what it owns

- **The slug decides which dashboard a spec manages.** Chartwright derives a dashboard's uuid from its slug, and a chart's uuid from the slug plus the chart's name. It doesn't record which person, team or file built a dashboard, so two specs with the same slug manage the same dashboard.
- **It changes only what it built.** `apply` refuses to touch a dashboard at the spec's slug if Chartwright didn't create it, and stops with "Pick a different slug". `absorb` and `restore` follow the same rule.
- **Charts that leave the spec are deleted.** When you remove or rename a chart in the spec, the next `apply` deletes the chart it had built, even if someone also added that chart to another dashboard. Charts Chartwright didn't build are never deleted.
- **Give each team its own slug prefix**, such as `finance-` or `ops-`, so that two teams' specs never share a slug.

## Every limit, with what to do instead

| What's true today | What to do instead |
|---|---|
| **Sign-in:** Chartwright signs in through Superset's username-and-password login API (database or LDAP accounts) or with Preset API tokens. It can't complete an SSO or OAuth sign-in, and accounts created through SSO have no Superset password | Use an account that has a database or LDAP password. On an instance set up for SSO, ask its admin whether the login API accepts such an account. If it doesn't, use an instance that allows database or LDAP login, or a Preset workspace with API tokens |
| **Roles:** all testing signs in with the Admin role. Restricted roles are untested | Use an Admin account, such as a dedicated service account, or try your own role on a test instance first |
| **Datasets:** datasets and database connections must already exist on each instance. Chartwright reads them and never creates them | Create them on each instance first, in Superset or with the tooling you already use. `chartwright check` lists every missing one |
| **Connection names:** each dataset is matched by its exact database connection name and table (and schema, when the spec gives one). There's no per-instance mapping | Where names differ between instances, keep one copy of the spec per instance, or rewrite the `dataset` blocks with a script ([MOVE-BETWEEN-INSTANCES.md](MOVE-BETWEEN-INSTANCES.md)) |
| **UI edits:** every `apply` writes the spec over the dashboard. Layout and chart settings go back to the spec, filters added in the UI are removed, and dashboard CSS, colour scheme and refresh interval are reset | Make lasting changes in the spec. Run `chartwright plan` first to see what someone changed in Superset, and `chartwright absorb` to copy dragged chart heights into the spec |
| **Charts added in the UI:** on Superset 4.1.4 and 5.0.0, a chart added in the UI to a managed dashboard makes the next `apply` fail at its linkage step. On 6.1.0 the chart is taken off the dashboard and left in Superset on no dashboard | Add new charts to the spec. If someone has already added one in the UI, take it off the dashboard in Superset's editor, then add it to the spec |
| **Chart names:** a chart's name is its identity. Renaming a chart in the spec deletes the old chart and creates a new one with a new id | Settle chart names before reports or links point at a chart. `plan` shows a rename as one chart removed and one added |
| **Decompile:** `decompile` lists most of what it can't carry over in its `losses`, but drops some settings without a note ([listed below](#settings-decompile-drops-without-a-note)) | Before you retire the original, compare it with the copy for those settings |
| **Taking over a dashboard made in the UI:** applying its decompiled spec at the original slug is refused, so you get a copy at a new slug, with new chart ids | Build the copy, point bookmarks, embedded links and scheduled reports at it, then delete the original in Superset ([CLONE-A-DASHBOARD.md](CLONE-A-DASHBOARD.md)) |
| **Decompile input:** `decompile` reads a live dashboard, not a ZIP export | Import the ZIP into a test instance and decompile it there |
| **Chart types and queries:** 15 chart types. Metrics are a saved metric or SUM, AVG, COUNT, COUNT_DISTINCT, MIN or MAX of a column. Chart filters are simple WHERE conditions. The filter bar has three filter types: value picker, time range and numeric range | Save other SQL as a metric or a virtual dataset in Superset, and name it in the spec |
| **`plan` detail:** `plan` names each changed chart but not the setting that changed. It compares only settings a spec can express, so a change to dashboard CSS doesn't show | To see which setting changed, run `chartwright decompile <slug> --profile <name> -o live.json` and diff `live.json` against your spec. Keep styling in the spec (`label_colors`, colour rules, text blocks), since `apply` clears dashboard CSS anyway |
| **Heights from the UI:** `absorb` copies heights only | Set widths in the spec: a chart's `width`, or its run length in a `sketch` |
| **Design fixes on a sketch:** `advise --fix` writes an explicit height that overrides the sketch, so the drawing no longer matches | To keep the drawing true, make a chart taller by repeating its line in the sketch |
| **MCP server:** it has no `restore`, `absorb`, `calibrate` or `compile` tool, and `build_dashboard` has no strict design gate. It does save a backup before every build of an existing dashboard | Run those from the command line, for example `chartwright restore <backup.zip> --profile <name>` |
| **One dashboard per spec:** each spec describes one dashboard, and each command takes one spec | Loop over a folder of specs in a shell script or a CI job |
| **Backups:** they're kept per profile name, not per instance, under `~/.config/chartwright/backups/<profile>/<slug>/` (or under `CHARTWRIGHT_BACKUP_DIR`). A backup holds one dashboard and its charts | Keep one profile name per instance, never point a name at a different instance, and restore with the profile that made the backup. To back up the whole instance, back up Superset's metadata database |
| **Automatic restore:** it runs only when something fails while `apply` prepares or runs the import, in-place chart updates included. If the linkage check, setting the filter scopes or a chart query fails, the new version stays live | Run `chartwright restore` with the backup path printed in the `apply` report |
| **Embedding:** embedding settings aren't in the spec, and whether they survive `apply` is untested | Turn embedding on in Superset, re-apply on a test instance, and confirm the embedded dashboard still loads before you rely on it |
| **Row-level security:** datasets filtered by row-level security are untested | Apply on a test instance with your row-level security rules and role, and check each chart's result in the `apply` report |
| **Template variables:** a spec has none | Write one spec per copy with a script that changes the slug and the `dataset` blocks ([CLONE-A-DASHBOARD.md](CLONE-A-DASHBOARD.md)) |
| **Deleting dashboards:** there's no delete command, and changing a spec's slug builds a new dashboard while the old one stays | Delete the dashboard in Superset, and its charts from Superset's chart list |
| **Scripting:** there's no documented Python API | Call the command line and read its JSON output and exit codes, or use the MCP server |
| **Superset releases:** tested on 4.1.4, 5.0.0 and 6.1.0 only | On any other release, apply your specs on a test instance first ([SUPERSET-VERSIONS.md](SUPERSET-VERSIONS.md)) |
| **Databases:** CI tests use Superset's example datasets, and Chartwright is also used against one production warehouse. Other database engines are untested | Apply on a test instance first. `apply` queries every chart, and its report names any chart that errors or comes back empty |

## Settings decompile drops without a note

- **Dashboard:** CSS, colour scheme and refresh interval.
- **Charts:** annotations, forecasts, y-axis bounds, a time range set on a chart with no time axis (a big number set to "Last 30 days" becomes all-time), rolling windows, time comparison, currency format, cache timeout, start-y-axis-at-zero, truncated metric, percentage calculation and colour scheme.
- **Filters:** scoping to one tab (the filter becomes dashboard-wide; for a value picker or numeric range, list the tab's charts in its `charts` field), exclude mode, dependent filters, pre-filters and descriptions.

## Related

- [SUPERSET-VERSIONS.md](SUPERSET-VERSIONS.md): what differs between 4.1.4, 5.0.0 and 6.1.0, and how to rebuild after an upgrade
- [CLONE-A-DASHBOARD.md](CLONE-A-DASHBOARD.md): decompile, and copies per team or tenant
- [MOVE-BETWEEN-INSTANCES.md](MOVE-BETWEEN-INSTANCES.md): profiles, sign-in and connection names
- [HISTORY-AND-ROLLBACK.md](HISTORY-AND-ROLLBACK.md): backups and `restore`
- [VERIFICATION.md](VERIFICATION.md): how it's tested, and the coverage limits
- [CONTRACTS.md](CONTRACTS.md): the Superset behaviour Chartwright relies on, cited to source
