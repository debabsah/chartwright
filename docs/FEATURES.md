# Features

Chartwright builds Apache Superset dashboards from a small file called a spec,
updates them from that same file later, and decompiles existing dashboards back
into one. Everything below works from that one file.

## Creating Dashboards with AI
- **Context to Dashboard**: Ask in plain words; the dashboard is built from what is in front of you.
    - The analysis you just ran, a KPI contract document, a metrics definition page.
    - The tables and views you were exploring: dbt-built models, ELT outputs, anything Superset knows as a dataset.
    - A screenshot of a dashboard in another BI tool, pointed at the same underlying data.
- **Reviewable Checkpoint**: The AI's output is a small spec file you can read, edit, and version like code.
- **Open AI Contract**: `chartwright schema` prints the full JSON Schema so any LLM or tool can generate valid specs.
- **MCP Server**: Eleven tools covering the whole lifecycle, usable from any MCP client.
- **Guardrails**: The dashboard is new; the data behind it must be real. Every dataset, column, and metric the AI references is confirmed to exist before anything is built, so a made-up column becomes a clear error message, never a broken chart.

## Dashboard Design
- **Deterministic Dashboards**: The same spec always produces the identical dashboard. Diff it in git, review it in a PR.
- **15 Chart Types**: big number, big number with trendline, line, bar, area, scatter, categorical bar, pie/donut, table, pivot table, heatmap, histogram, funnel, treemap, and mixed (bars and a line on two value axes, over time or over categories).
- **Metrics As You Write Them**: Saved Superset metrics, `SUM(col)`-style aggregates, `COUNT(*)`, with inline renames (`MAX(pct_of_goal) AS % of Goal`).
- **Filters and Formatting**: Per-chart WHERE conditions, a native filter bar, and formatting for table and pivot cells.
    - Filter bar: value pickers, a time range with an optional starting range, and numeric sliders.
    - Scope value pickers and sliders to specific charts; the time range applies to the whole dashboard.
    - Solid green/amber/red colour rules on pivot and table cells.
    - On Superset 6.1+, a table rule can read one column and paint another, or the whole row: a number coloured by the status beside it.
    - Hidden table columns, a fixed ascending table sort, d3 number and date formats.
- **Cross-Filtering, Spec-Owned**: `"cross_filters": true` on the dashboard block turns on Superset's click-to-filter (a value clicked in one chart filters every chart whose dataset has that column, across tabs). Off by default; a toggle made in the UI is drift that `plan` reports and `apply` repairs.
- **No Empty First Load**: New charts open on your full data range, so a narrow default time window never hides everything on the first paint. On a large dataset that full range is a lot to draw, so give the filter bar a time range with a default; `chartwright advise` tells you when a dashboard has nothing bounding its dates.

## Layout Design
- **ASCII Layout Design**: Draw the layout straight from the terminal: `"KKKK LLLLLLLL"` is a KPI card beside a wide line chart.
    - Sizes are ratios drawn as text: more letters make a chart wider, more lines of text make it taller.
    - Stack letters vertically for columns; `.` marks deliberate empty space.
    - An unclear sketch gets a message saying exactly what to fix, never a guess.
    - Every rule drawn and compiled: [the layout guide](LAYOUT-GUIDE.md).
- **Precise Sizing**: Set exact widths and heights per chart (markdown blocks down to one 8 px grid row: `"height": 1.6` is 64 px), or drag a chart taller in the UI and `chartwright absorb` writes the new height back into the spec; widths are a one-line edit in the layout.
- **Rows, Tabs, and Notes**: Even or custom row splits, titled tabs (with one level of sub-tabs, e.g. a sub-tab per row of a scorecard), and markdown blocks for headers and notes.
- **Footer**: `layout.footer` rows sit below everything, outside any tab, so a tabbed dashboard shows them under every tab: a branding strip, a data note, a contact line.

## The Design Brain
- **Codified BI/UX Practice**: An optional layer holding what Few, Tufte and IBCS teach about reading a dashboard, plus the Superset rendering quirks that break it, on by default and off with one flag ([full reference](DESIGN-BRAIN.md)).
- **The Brief**: `chartwright brief` prints design guidance for the AI (or you) to read before writing a spec: budgets, chart choice, and composition, tuned to an audience preset (`executive`, `analytical`, or `operational`).
- **The Critic**: `chartwright advise` reviews a finished spec: readable minimum sizes, layout composition (KPIs first, fold budgets, row density), chart-choice limits, narrative polish. `--fix` applies the safe geometry subset; `--profile` adds data-aware checks (a time axis on a non-temporal column, a pie hiding 40 slices).
- **Deliberate Exceptions, Visible**: Suppress any rule per dashboard or per chart in the spec's `design` block; suppressions are reported, never silent.
- **House Style**: A `design.yaml` overlay tunes thresholds, disables rules, and appends org guidance to the brief, so a deployment can set its own standards without forking the rulebook.
- **Heights Calibrated From Your Own Dashboards**: Heights you polish in the UI flow back via `absorb`; `chartwright calibrate` mines them and updates the recommended heights the brief and autofixes use.
- **Design Audits of Legacy Dashboards**: `decompile` + `advise` grades any UI-built dashboard against the rulebook.
- **One-Shot Redesign**: `chartwright redesign <dashboard>` decompiles a live dashboard, audits it, applies the safe geometry fixes, and writes the redesigned spec. A tool-built dashboard is redesigned in place; anything else comes back under a new slug and applies side by side, leaving the original untouched.

## Dashboards as Code
- **Drift Detection**: `chartwright plan` diffs the spec against the live dashboard: charts, filters, scopes, title, layout.
- **Take Over Existing Dashboards in Place**: `chartwright adopt` turns a dashboard built in the UI into a spec that manages that same dashboard: its address, id and chart ids stay, so links and scheduled reports pointing at the dashboard keep working. Filter and tab ids are replaced on the first apply, so a link that saved a filter state or opened a specific tab may need saving again. The first apply rewrites each chart to what its spec produces, so chart options a spec can't hold go back to the tool's defaults; `plan` names the charts and dashboard settings (such as CSS) it can see changing.
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
- **Ownership Guard**: The tool only changes dashboards it created or that you adopted with `chartwright adopt`. An adopted dashboard is changed only at the address it was adopted from; if it moves, run `chartwright adopt` on it again. A chart that was on the dashboard when you adopted it is taken off when it leaves the spec, never deleted; charts the tool created are deleted.

## Enterprise Ready
- **Multiple Instances**: Sandbox, staging, and production as profiles in one file.
- **Secrets Stay Out of Your Profiles**: Point each profile at where its secret already lives.
    - Self-hosted Superset signs in with a username and password (database or LDAP provider): take the password from an env var (any name) or your credential manager (1Password, macOS Keychain, sops). On SSO-only Superset, ask your admin for a service account with password login enabled.
    - Preset-hosted workspaces (preset.io) sign in with an API token and secret: take them from env vars, or reuse the credentials preset-cli already stored.
- **What the AI Can See**: The AI proposes; the tool verifies, using your own Superset login. Verification reads names (datasets, columns, metrics), not rows; the AI never queries your warehouse.
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
| `chartwright brief` | Print the design guidance to read before writing a spec |
| `chartwright advise` | Review a spec against the design rulebook; `--fix` applies the safe geometry repairs |
| `chartwright compile` | Build the import bundle, no server needed |
| `chartwright check` | Verify every dataset, column, and metric against a live instance, read-only |
| `chartwright apply` | Build, import, and verify the dashboard end to end |
| `chartwright plan` | Show what differs between the spec and the live dashboard |
| `chartwright decompile` | Turn a live dashboard into a spec |
| `chartwright adopt` | Take over an existing dashboard in place: a spec that updates the same dashboard |
| `chartwright redesign` | Decompile a live dashboard, audit it, and write the repaired spec |
| `chartwright absorb` | Pull height polish made in the UI back into the spec |
| `chartwright calibrate` | Propose recommended heights from your absorb history |
| `chartwright restore` | Bring back a backed-up dashboard, completely |

## Testing and Evidence
- **The full offline suite** on Linux and Windows on every push, and on every pull request ([exact counts](VERIFICATION.md)).
- **Live CI against real Superset 4.1.4, 5.0.0, and 6.1.0** on every push: full apply, lifecycle soak, stale-tab adversary, fault injection.
- **500-cycle soak** passed on the oldest and newest supported versions.
- **Chart options verified against Superset's own source code** for every supported version, so a Superset change surfaces here before it reaches your dashboards.
- Full evidence: `docs/VERIFICATION.md`; source citations: `docs/CONTRACTS.md`.

## Deliberately Not Included
- Exotic chart types (maps, sankey, gauge): added when a real dashboard needs one (ask); until then those dashboards live on in the UI, untouched.
- Per-chart cross-filter scoping (which charts emit or receive): the on/off switch is in the spec; finer scoping waits for real demand.
