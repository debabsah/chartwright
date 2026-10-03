# Creating dashboards from code

You write, or generate, a short JSON spec that names a dashboard's charts, filters and layout, and build it into a Superset dashboard without writing Superset's import files or layout JSON yourself. Every command returns JSON and an exit code, so your scripts can generate, check and build dashboards in a loop.

## Read the schema

```bash
chartwright schema > spec.schema.json
```

`chartwright schema` prints the JSON Schema every spec follows; generate and check your specs against it. The same schema is in the repository at `schema/dashboard_spec.schema.json`, and a test keeps the file identical to the command's output.

The main fields:

- `spec_version`: always `"1"`.
- `dashboard`: `title` and `slug` (lowercase kebab-case). The slug is the dashboard's identity: building a spec with the same slug updates the same dashboard. Optional: `cross_filters`, `label_colors`.
- `charts`: each chart has
  - `name`, unique in the dashboard and part of the chart's identity;
  - `type`, one of 15: `big_number_total`, `big_number_trend`, `timeseries_line`, `timeseries_bar`, `timeseries_area`, `timeseries_scatter`, `bar`, `pie`, `table`, `pivot_table`, `heatmap`, `histogram`, `funnel`, `treemap`, `mixed`;
  - `dataset`: the `database` connection name and `table`, plus `schema` where the table name alone is ambiguous;
  - metrics written as an aggregate, `SUM`, `AVG`, `COUNT`, `COUNT_DISTINCT`, `MIN` or `MAX` of a column (`SUM(amount)`, optionally `SUM(amount) AS Revenue`), or the name of a saved metric;
  - optional `filters` (WHERE conditions on that chart), `width` in twelfths of the page, and `height` in 40 px units.
- `filters`: the dashboard's filter bar, with `select`, `time_range` and `range` filters.
- `layout`: exactly one of `rows`, `tabs` or `sketch`, plus an optional `footer`. In `rows`, charts without a `width` share the row equally.
- `design`: optional; the review's `audience` and the rule ids to `ignore`.

A small spec, two numbers above a bar chart, with a region filter:

```json
{
  "spec_version": "1",
  "dashboard": {"title": "Orders by Region", "slug": "orders-by-region"},
  "charts": [
    {"name": "Orders", "type": "big_number_total", "metric": "COUNT(*)", "number_format": ",.0f",
     "dataset": {"database": "warehouse", "table": "orders"}},
    {"name": "Revenue", "type": "big_number_total", "metric": "SUM(amount)", "number_format": "$,.0f",
     "dataset": {"database": "warehouse", "table": "orders"}},
    {"name": "Revenue by Region", "type": "bar", "x_column": "region", "metrics": ["SUM(amount)"],
     "orientation": "horizontal", "row_limit": 10, "height": 7,
     "dataset": {"database": "warehouse", "table": "orders"}}
  ],
  "filters": [
    {"type": "select", "name": "Region", "column": "region",
     "dataset": {"database": "warehouse", "table": "orders"}}
  ],
  "layout": {"rows": [["Orders", "Revenue"], ["Revenue by Region"]]}
}
```

Change `warehouse`, `orders`, `amount` and `region` to a dataset and columns on your Superset. To draw the layout as text instead of `rows`, see [LAYOUT-GUIDE.md](LAYOUT-GUIDE.md).

## Check and build

```bash
chartwright validate orders-by-region.json                    # fields and layout
chartwright advise orders-by-region.json                      # readability review
chartwright check orders-by-region.json --profile dev         # each dataset, column and metric is on the instance
chartwright apply orders-by-region.json --profile dev         # build, then run each chart's query
```

`validate` and `advise` need no Superset. `check` lists every missing reference in one response. `apply` runs the same check before it writes anything, then confirms each chart is on the dashboard and runs its query.

## Inspect the import ZIP

```bash
chartwright compile orders-by-region.json -o orders-by-region.zip
```

`compile` writes the chart and dashboard files of the import ZIP without contacting Superset, so you can read what a spec turns into. The same spec always compiles to the same bytes, so any difference between two ZIPs comes from the specs. Its dataset ids are stand-ins, so use the ZIP for debugging only, and build with `apply`, which looks up the real ids. Without `-o`, the file is named after the slug.

## Read the results

Every command except `brief` prints JSON on stdout; `brief` prints the design brief as Markdown. A failed command names the `stage` where it stopped, and lists what it found. Exit codes:

| Command | Exit 0 | Exit 1 |
|---|---|---|
| `validate` | The spec is well formed | Unreadable file, or field and layout errors |
| `compile` | ZIP written | Unreadable file, or field and layout errors |
| `check` | Each dataset, column and metric was found | Missing references, all listed; or design findings with `--design strict` |
| `plan` | No differences between spec and live dashboard | Any difference; no dashboard at the slug yet; missing references; or a dashboard Chartwright didn't build at the slug |
| `apply` | Built, and every chart's query ran (charts returning no rows are named in `warnings`) | Stopped at the step named in `stage` |
| `advise` | No error findings (with `--strict`, no warnings either) | Findings that block |

- Commands that sign in exit 1, with JSON, on profile or sign-in errors.
- Every command exits 2 on a mistyped command or option; that message goes to stderr as plain text.
- `chartwright <command> --help` lists each command's options.

## Build many dashboards

Each spec holds one dashboard, and each command takes one spec. Loop over a folder to build several:

```bash
for spec in specs/*.json; do
  chartwright validate "$spec" > /dev/null || { echo "invalid: $spec"; exit 1; }
done
mkdir -p out
for spec in specs/*.json; do
  chartwright apply "$spec" --profile dev > "out/$(basename "$spec")" || echo "failed: $spec"
done
```

Each build report lands in `out/`; a failed one names its `stage` and carries `resolution_errors` or `import_detail`.

## Drive it from your own code

Run Chartwright through its CLI or its MCP server. No Python API is documented, and the package's internal modules can change between releases; call the CLI from your program and parse its JSON:

```python
import json, subprocess

run = subprocess.run(["chartwright", "validate", "orders-by-region.json"], capture_output=True, text=True)
result = json.loads(run.stdout)
print(run.returncode, result.get("errors", "ok"))
```

To drive it from an AI agent or another MCP client, use the server's 10 tools, which run the same code as the CLI commands and return JSON reports ([AI-AGENTS.md](AI-AGENTS.md)).

## Limits

- One dashboard per spec and per command; loop over a folder of specs, as above.
- A spec names each dataset by its database connection name, table and schema exactly as they are on the instance; where names differ between instances, generate one copy of the spec per instance.
- Datasets and database connections aren't created from the spec; create them in Superset first, and `check` confirms they're there.
- `compile` output carries stand-in dataset ids; build with `apply`.

## Related

- [CONTRACTS.md](CONTRACTS.md): how Superset reads the import files and layout JSON, cited to source
- [LAYOUT-GUIDE.md](LAYOUT-GUIDE.md): `rows`, `tabs` and `sketch` layouts
- [DEPLOY-FROM-GIT.md](DEPLOY-FROM-GIT.md): running these commands in CI
- [CLONE-A-DASHBOARD.md](CLONE-A-DASHBOARD.md): generating copies of a spec for other datasets
- [AI-AGENTS.md](AI-AGENTS.md): the MCP server
- [LIMITS.md](LIMITS.md): every limit, with what to do instead
