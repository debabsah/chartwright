# Chartwright

A dashboard compiler for Apache Superset. Describe the dashboard once, in a
small text file called a spec, and the `chartwright` command line creates it,
verifies it, and keeps it that way.

![An operations dashboard over NYC yellow-taxi data: KPI cards with unit subtitles, daily trend charts, an hour-by-weekday demand heatmap, a payment donut, borough and zone rankings, and a trip-distance histogram, behind a three-filter bar](docs/images/nyc-taxi-operations.png)

*3.9 million yellow-taxi trips (NYC TLC trip records, May 2026), 11 verified
charts, three filters, one `chartwright apply`. An AI wrote
[the spec](examples/nyc_taxi_operations.json) from a one-paragraph request:
[the request and rebuild steps](examples/README.md).*

A dashboard that lives in a file gets the workflow code already has: review it
in a pull request, rebuild it identically, diff it against what is live, and
bring it back after a bad change. Write the spec yourself or ask an AI for one;
either way, every dataset, column, and metric the spec names is confirmed to
exist before anything is built, and every chart is checked to load with data
after.

## What a spec looks like

```json
{
  "spec_version": "1",
  "dashboard": {"title": "Sales Overview", "slug": "sales-overview"},
  "charts": [
    {
      "name": "Total Sales",
      "type": "big_number_total",
      "dataset": {"database": "examples", "table": "cleaned_sales_data"},
      "metric": "SUM(sales)"
    },
    {
      "name": "Sales Over Time",
      "type": "timeseries_line",
      "dataset": {"database": "examples", "table": "cleaned_sales_data"},
      "metrics": ["SUM(sales)"],
      "time_column": "order_date"
    }
  ],
  "layout": {"rows": [["Total Sales"], ["Sales Over Time"]]}
}
```

`chartwright apply` on this file signs in to your Superset, confirms the
`cleaned_sales_data` table and the `sales` and `order_date` columns exist,
builds a KPI card above a line chart, and finishes by running each chart's
query once to prove it shows data. A misspelled column or a missing table is a
clear error naming the problem, before anything is created.

## Quick start

```bash
git clone https://github.com/debabsah/chartwright.git
cd chartwright
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Python 3.11 or newer; three dependencies; Windows, macOS, and Linux.

Create `~/.config/chartwright/profiles.toml` pointing at your Superset:

```toml
[prod]
base_url = "https://superset.your-company.com"
username = "your-username"
password_env = "CHARTWRIGHT_PROD_PASSWORD"
```

Point the example spec's `dataset` lines at a table your Superset already
knows, save it as `sales_overview.json`, then:

```bash
chartwright check sales_overview.json --profile prod   # read-only: verify every reference
chartwright apply sales_overview.json --profile prod   # build, import, verify
```

No Superset to point at? `sandbox/up.sh` boots a disposable Superset 6.1.0
with example data on `localhost:8098` (Docker required; sign-in is
admin/admin), and `tests/fixtures/kitchen_sink.json` applies to it as-is.

## What you get

- **Deterministic dashboards**: the same spec always produces the identical
  dashboard. Diff it in git, review it in a PR.
- **Verified at every step**: every reference is checked before anything is
  written, and every chart's query runs once to prove it shows data; failures
  say what went wrong and where.
- **Dashboards as code, in both directions**: `chartwright decompile` turns any live
  dashboard into a spec; `chartwright plan` shows what differs between the spec and
  the live dashboard, ready as a CI gate; `chartwright compile` builds the import
  bundle offline, no server needed.
- **Safety built in**: every apply backs up the previous state first; a failed
  apply restores it automatically; the tool only ever overwrites dashboards it
  created, and a hand-built dashboard is adopted by decompiling it into a spec
  first.
- **The full design surface**: 14 chart types, metrics as you write them,
  per-chart filters, a native filter bar, tabs, markdown notes, and layouts
  you can draw as ASCII sketches.
- **Environment promotion**: specs name their data (connection, schema,
  table), so the same file applies to dev, staging, and production unchanged.

Every capability, with the CLI verb reference: [docs/FEATURES.md](docs/FEATURES.md).

## Creating dashboards with AI

- **Claude Code skill**: `python install-skill.py`, then ask for a dashboard
  in plain words; the AI writes the spec, and the tool verifies and builds it.
- **MCP server**: `chartwright-mcp` (installed with `pip install -e ".[mcp]"`) exposes
  six tools covering the whole lifecycle, usable from any MCP client.
- **Open contract**: `chartwright schema` prints the spec's JSON Schema, so any LLM or
  tool can generate valid specs.
- **Guardrails**: the AI proposes; the tool verifies, using your own Superset
  login. Verification reads names (datasets, columns, metrics), not rows.

## Drawing layouts as text

The spec's `sketch` is the dashboard's shape, drawn with characters that you
and a model can both write: each letter is a chart, and repeating a sketch
line makes its charts taller.

```text
KKKK LLLLLLLL
.... LLLLLLLL
```

![A short chart beside one twice as tall](docs/images/sketches/taller.svg)

`L` spans both lines, so it renders twice as tall as `K`; the dots keep the
space under `K` deliberately empty. Every rule, drawn and explained:
[docs/LAYOUT-GUIDE.md](docs/LAYOUT-GUIDE.md).

## Testing and evidence

Every push runs 123 tests on Linux and Windows, plus the full pipeline (apply,
lifecycle soak, stale-tab adversary, fault injection) against real Superset
4.1.4, 5.0.0, and 6.1.0 containers. Chart options are checked against
Superset's own source for every supported version, so a Superset change is
caught in our tests before it reaches your dashboards.

Full evidence: [docs/VERIFICATION.md](docs/VERIFICATION.md). Source citations
for every Superset behavior the tool relies on:
[docs/CONTRACTS.md](docs/CONTRACTS.md).

## Documentation

- [docs/FEATURES.md](docs/FEATURES.md): every capability, with the CLI verb
  reference
- [docs/LAYOUT-GUIDE.md](docs/LAYOUT-GUIDE.md): drawing layouts as text,
  every rule illustrated
- [docs/VERIFICATION.md](docs/VERIFICATION.md): what is tested, what it
  caught, and how to reproduce it
- [docs/CONTRACTS.md](docs/CONTRACTS.md): the Superset behaviors the tool
  depends on, cited to source at each supported version

---

Apache Superset, Superset, and the Superset logo are trademarks of the Apache
Software Foundation. No endorsement by the ASF is implied.
