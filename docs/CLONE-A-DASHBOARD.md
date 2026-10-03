# Clone a dashboard onto another dataset, team or tenant

Copy a spec, give the copy its own slug and point it at other datasets, and Chartwright builds a separate dashboard that reads that data. A short loop gives every tenant its own copy and keeps all the copies in step with one source file. Start from a spec you already have, or generate one from any dashboard on your instance, including one built in the UI.

## Copy a spec onto other data

1. Copy the spec to a new file.
2. Change `dashboard.slug`, and the title if you like.
3. Change the `dataset` block on each chart, and on each value-picker or range filter. A dataset is named by its database connection, schema and table.
4. Run `chartwright check copy.json --profile prod`, then `chartwright apply copy.json --profile prod`.

| Change in the copy | Effect |
|---|---|
| `dashboard.slug` | A separate dashboard. Chart ids come from the slug and the chart name, so the copy gets charts of its own and the original is left as it is |
| `dashboard.title` | The name people see in Superset |
| `database`, `schema` or `table` in a `dataset` block | That chart or filter reads the new dataset. `check` confirms the dataset has every column and saved metric the spec names |

Two specs with the same slug manage the same dashboard, so give every copy a slug of its own, such as a team or tenant suffix.

## One copy per tenant

This loop assumes each tenant's tables sit in a schema of their own, with the same table names in every schema. It writes one spec per tenant from `orders.json` (the template), checks it, and builds it:

```bash
for t in acme globex; do
  jq --arg t "$t" '
    .dashboard.slug = "orders-\($t)"
    | .dashboard.title = "Orders: \($t)"
    | (.. | objects | select(has("table")) | .schema) = $t
  ' orders.json > "orders-$t.json"
  chartwright check "orders-$t.json" --profile prod --design off \
    && chartwright apply "orders-$t.json" --profile prod
done
```

- The jq line sets the schema in every `dataset` block, charts and filters alike.
- Where each tenant has its own database connection instead, set `.database`; where each has its own table, set `.table`.
- Tenant names become part of the slug, which takes lowercase letters, digits and hyphens. `chartwright validate` rejects any other slug before you touch Superset.
- Each tenant's datasets must already exist in Superset: register each tenant's table as a dataset first. When one is missing, `check` names it, lists tables of the same name in other schemas, and exits 1, so the loop skips that tenant's apply and moves on.

## Update every copy

Edit `orders.json` and run the loop again. Each apply finds its copy by slug and updates it in place:

- charts keep their ids, so links and filter scopes keep working;
- a chart removed from the template is deleted from every copy;
- a chart renamed in the template is deleted and created again under its new name in every copy;
- each copy is backed up before it changes.

To see what a run will change first, run `chartwright plan orders-acme.json --profile prod`. It also shows edits someone made to that copy in the UI, which the next apply overwrites.

## Vary the copies with a script

A spec holds literal values only: every slug, title and dataset name is written out in full, so the file you review is the dashboard that gets built. To vary anything per copy, generate the copies with a script, as the jq loop does, or with any language that writes JSON. Edit only the template, regenerate, and run `chartwright validate` on each generated file.

## Start from a dashboard you already have

Use this for any dashboard on your instance, including one built in the UI.

1. Generate its spec: `chartwright decompile regional-sales --profile prod -o regional-sales.json`, using the slug or the numeric id. The spec goes to the file and the `losses` list to the terminal.
2. Read `losses` (below), then check the settings it doesn't mention.
3. Change `dashboard.slug`, for example to `regional-sales-v2`. Applying at the original slug is refused ("Pick a different slug"), because Chartwright didn't build that dashboard.
4. To clone it onto other data at the same time, change the `dataset` blocks as above.
5. Run `chartwright validate regional-sales.json`, then `check` and `apply` with your profile.
6. Compare the new dashboard with the original. Then point bookmarks, embedded links and scheduled reports at the new one, and delete the original in Superset.

For a dashboard Chartwright built, edit the spec it was built from. If that spec is lost, `decompile` works there too, and applying the result at the same slug updates the dashboard; read `losses` first, since charts stacked beside a tall one in a `sketch` come back as separate rows.

`losses` lists what the spec can't carry, one entry per item, naming the chart, filter or layout part:

- charts of a type outside the 15 Chartwright builds, or whose dataset can't be found, which are skipped;
- chart settings, filter defaults and filter chart scopes that weren't preserved, by name;
- layout changes: columns flattened into rows, nested tabs flattened, sizes rounded.

These settings are dropped without a note, so check them on the original before you retire it:

- on the dashboard: CSS, colour scheme and refresh interval;
- on charts: annotations, forecasts, y-axis bounds, a time range set on a chart with no time axis (a big number set to "Last 30 days" becomes all-time), rolling windows, time comparison, currency format, cache timeout, start-y-axis-at-zero, truncate metric, percentage calculation and colour scheme;
- on filters: scoping to one tab (the filter then applies to the whole dashboard), exclude mode, dependent filters, pre-filters and descriptions.

None of these is a spec field (the nearest is `y_axis_max` on line charts; `chartwright schema` lists every field). Where one matters, keep the original dashboard for it, or rework the chart with the fields the spec has. Once a spec manages the dashboard, make changes in the spec and apply them, since each apply overwrites edits made in the UI. To move the copy to other instances, see [MOVE-BETWEEN-INSTANCES.md](MOVE-BETWEEN-INSTANCES.md).

## Embedding and row-level security

Both are untested with Chartwright, and neither is in the spec. Confirm them on your own test instance before you rely on them:

- **Embedding:** build a copy, enable embedding on it in Superset, and note its settings. Change the spec, apply it again, then confirm the embedding settings are unchanged and the embedded dashboard still loads in your app.
- **Row-level security:** the rules live on datasets in Superset, and Chartwright never reads or changes them. Add a rule for a test role on one tenant's dataset, sign in as a user with that role, and confirm that tenant's copy shows only the rows the rule allows.

## Limits

| What's true today | What to do instead |
|---|---|
| A spec has no template variables | Generate the copies from one template with a script, as in the loop above |
| One dashboard per spec and per command | Loop over the copies, as above |
| Each copy's datasets must already exist on the instance | Register them in Superset first; `check` lists the missing ones |
| A dashboard Chartwright didn't build can't be updated at its current slug | Decompile it and build the copy at a new slug |
| `decompile` reads a dashboard from a live instance, not from a ZIP export | Import the ZIP into your test instance and decompile it there |
| Some settings are dropped by `decompile` without a note (listed above) | Check them on the original before you retire it, and keep the original where one matters |
| A chart Chartwright built that leaves the spec is deleted, even if someone also added it to another dashboard | To show a chart on two dashboards, put it in both specs; each builds a chart of its own |
| A chart added in the UI to a copy makes `apply` fail at linkage on 4.1.4 and 5.0.0; on 6.1.0 the chart is taken off the dashboard | Add charts to the template and run the loop |
| Embedding and row-level security are untested | Confirm them on your test instance, as above |

## Related

- [MOVE-BETWEEN-INSTANCES.md](MOVE-BETWEEN-INSTANCES.md): profiles, sign-in, and moving a spec between dev, staging and prod
- [DEPLOY-FROM-GIT.md](DEPLOY-FROM-GIT.md): run the loop from CI
- [HISTORY-AND-ROLLBACK.md](HISTORY-AND-ROLLBACK.md): backups and `restore`
- [DASHBOARDS-FROM-CODE.md](DASHBOARDS-FROM-CODE.md): the spec's fields, JSON output and exit codes for scripting
- [VERIFICATION.md](VERIFICATION.md): how it's tested
