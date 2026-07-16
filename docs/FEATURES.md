# Features

Chartwright builds Apache Superset dashboards from a small text file called a spec:
describe the dashboard once, and Chartwright creates it, verifies it, and keeps it
that way. Everything below works from that one file.

## AI-Native Dashboard Creation
- **Context to Dashboard**: Ask in plain words; the dashboard is built from what is in front of you.
    - The analysis you just ran, a KPI contract document, a metrics definition page.
    - The tables and views you were exploring: dbt-built models, ELT outputs, anything Superset knows as a dataset.
    - A screenshot of a dashboard in another BI tool, pointed at the same underlying data.
- **Reviewable Checkpoint**: The AI's output is a small spec file you can read, edit, and version like code.
- **Open AI Contract**: `chartwright schema` prints the full JSON Schema so any LLM or tool can generate valid specs.
- **MCP Server**: Six tools covering the whole lifecycle, usable from any MCP client.
- **Guardrails**: The dashboard is new; the data behind it must be real. Every dataset, column, and metric the AI references is confirmed to exist before anything is built, so a made-up column becomes a clear error message, never a broken chart.

## Dashboard Design
- **Deterministic Dashboards**: The same spec always produces the identical dashboard. Diff it in git, review it in a PR.
- **14 Chart Types**: big number, big number with trendline, line, bar, area, scatter, categorical bar, pie/donut, table, pivot table, heatmap, histogram, funnel, treemap.
- **Metrics As You Write Them**: Saved Superset metrics, `SUM(col)`-style aggregates, `COUNT(*)`, with inline renames (`MAX(pct_of_goal) AS % of Goal`).
- **Filters and Formatting**: Per-chart WHERE conditions, a native filter bar (value pickers, a time range with an optional starting range, numeric sliders, each scopable to specific charts), green/amber/red thresholds on pivot cells, d3 number and date formats.
- **No Empty First Load**: New charts open on your full data range, not a default time window that can hide everything.

## Layout Design
- **ASCII Layout Design**: Draw the layout straight from the terminal: `"KKKK LLLLLLLL"` is a KPI card beside a wide line chart.
    - Sizes are ratios drawn as text: more letters make a chart wider, more lines of text make it taller.
    - Stack letters vertically for columns; `.` marks deliberate empty space.
    - An unclear sketch gets a message saying exactly what to fix, never a guess.
    - Every rule drawn and compiled: [the layout guide](LAYOUT-GUIDE.md).
- **Precise Sizing**: Set exact widths and heights per chart, or drag a chart taller in the UI and `chartwright absorb` writes the new height back into the spec; widths are a one-line edit in the layout.
- **Rows, Tabs, and Notes**: Even or custom row splits, titled tabs, and markdown blocks for headers and notes.

## The Design Brain
- **A Visual Designer On Call**: An optional intelligence layer that knows BI/UX practice (Few, Tufte, IBCS) and Superset's rendering quirks, on by default and off with one flag ([full reference](DESIGN-BRAIN.md)).
- **The Brief**: `chartwright brief` prints design guidance tuned to an audience preset (`executive` / `analytical` / `operational`) — budgets, chart choice, composition — for the AI (or you) to read before writing a spec.
- **The Critic**: `chartwright advise` reviews a finished spec: readable minimum sizes, layout composition (KPIs first, fold budgets, row density), chart-choice limits, narrative polish. `--fix` applies the safe geometry subset; `--profile` adds data-aware checks (a time axis on a non-temporal column, a pie hiding 40 slices).
- **Deliberate Exceptions, Visible**: Suppress any rule per dashboard or per chart in the spec's `design` block; suppressions are reported, never silent.
- **House Style**: A `design.yaml` overlay tunes thresholds, disables rules, and appends org guidance to the brief — no fork of the rulebook.
- **It Learns From You**: Heights you polish in the UI flow back via `absorb`; `chartwright calibrate` mines them and updates the recommended heights the brief and autofixes use.
- **Design Audits of Legacy Dashboards**: `decompile` + `advise` grades any UI-built dashboard against the rulebook.

## Dashboards as Code
- **Drift Detection**: `chartwright plan` diffs the spec against the live dashboard: charts, filters, scopes, title, layout.
- **Adopt Existing Dashboards**: Turn any dashboard built in the UI into a spec with `chartwright decompile`; anything it cannot carry over is listed, so you know exactly what stayed in the UI.
- **Lossless Round-Trips**: Tool-built dashboards decompile back to their exact spec.
- **Targeted Edits**: Replace, rename, resize, or remove one chart and re-apply; old charts are cleaned up, never orphaned.
- **Stable Identity**: Chart ids never change across re-applies, so links, scopes, and open browser tabs stay valid.

## CI/CD and Promotion
- **Environment Promotion**: Specs name their data (connection, schema, table), so the same file applies to dev, staging, and production unchanged; nothing in it is tied to one instance.
- **PR-Gated Dashboard Changes**: Specs live in git; `chartwright plan` passes when the live dashboard matches the spec and fails when it drifted, ready as a merge gate; read-only `chartwright check` runs safely on any schedule.
- **Offline Compilation**: Build the import bundle with `chartwright compile`, no server needed; the output is reproducible byte-for-byte.
- **Instance Migration**: Decompile from one Superset, apply to another.
- **Git as the Source of Truth**: A lost or mangled dashboard is one re-apply away from its spec.

## Safety and Recovery
- **Automatic Backups**: Every apply saves the previous state first, no flag needed.
- **Complete Restore**: `chartwright restore` brings back the dashboard, chart settings, and filter scopes.
- **Self-Healing Applies**: A failed apply restores the previous state automatically.
- **Stale-Tab Protection**: An old browser tab writing back stale state is detected by `plan` and repaired by `apply`.
- **Verified at Every Step**: References are checked before anything is written, the finished dashboard is compared chart-by-chart against the spec, and every chart's query is run once to prove it shows data; any failure says what went wrong and where.
- **Ownership Guard**: The tool only ever overwrites dashboards it created. To edit a hand-built dashboard, adopt it first (decompile it into a spec, then apply); the original stays untouched.

## Enterprise Ready
- **Multiple Instances**: Sandbox, staging, and production as profiles in one file.
- **Credentials Stay Out of Files**: Usernames and passwords from env vars (any names) or your credential manager (1Password, macOS Keychain, sops).
- **Your Data Stays Put**: The AI proposes; the tool verifies, using your own Superset login. Verification reads names (datasets, columns, metrics), not rows; the AI never queries your warehouse.
    - The post-apply data check keeps a row count and discards the rows.
- **Corporate Networks**: Custom CA bundles, proxies, LDAP auth, internal pip mirrors (only 3 dependencies).
- **Works Where You Work**: Windows, macOS, Linux; PowerShell and git bash; run from any directory; the Claude Code skill installs by copy, no admin rights.

## More Ways to Use It
- **Dashboard Documentation**: Decompile any dashboard into a readable inventory of its charts, metrics, and filters.
- **Cloning and Templating**: Copy a spec, change the name and the data it points at, apply. A proven dashboard becomes a starting point.
- **Programmatic Dashboards**: Specs are JSON; generate or edit them with scripts, one dashboard per file.
- **Drift Audits**: Run `plan` on a schedule to catch UI edits that diverged from the reviewed spec.
- **Training and Demo Environments**: Boot a sandbox, apply a spec, and every student or demo gets the identical dashboard.
- **Safe Experiments**: Try a redesign under a new name, compare side by side with the original, delete nothing.

## CLI Verbs
| Verb | What it does |
|---|---|
| `chartwright schema` | Print the spec's JSON Schema, the contract for people and AIs |
| `chartwright validate` | Check a spec against the schema, offline |
| `chartwright compile` | Build the import bundle, no server needed |
| `chartwright check` | Verify every dataset, column, and metric against a live instance, read-only |
| `chartwright apply` | Build, import, and verify the dashboard end to end |
| `chartwright plan` | Show what differs between the spec and the live dashboard |
| `chartwright decompile` | Turn a live dashboard into a spec |
| `chartwright absorb` | Pull height polish made in the UI back into the spec |
| `chartwright restore` | Bring back a backed-up dashboard, completely |

## Testing and Evidence
- **125 tests** on Linux and Windows on every push.
- **Live CI against real Superset 4.1.4, 5.0.0, and 6.1.0** on every push: full apply, lifecycle soak, stale-tab adversary, fault injection.
- **500-cycle soak** passed on the oldest and newest supported versions.
- **Chart options verified against Superset's own source code** for every supported version, so a Superset change is caught in our tests before it reaches your dashboards.
- Full evidence: `docs/VERIFICATION.md`; source citations: `docs/CONTRACTS.md`.

## Deliberately Not Included
- Exotic chart types (maps, sankey, gauge): added when a real dashboard needs one (ask); until then those dashboards live on in the UI, untouched.
- API keys: open-source Superset has none (Preset does); its API signs in with a username and password, which is exactly what the tool uses (database or LDAP providers). On SSO-only shops, ask your admin for a service account with password login enabled.
- Cross-filter configuration from the spec: clicking a chart to filter the others still works on every applied dashboard; designing custom cross-filter scoping in the spec waits for real demand.
