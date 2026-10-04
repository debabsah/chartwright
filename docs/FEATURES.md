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
- **MCP Server**: Eleven tools covering the whole lifecycle, usable from any MCP client. Failures come back as the same typed JSON errors the CLI prints.
- **Guardrails**: The dashboard is new; the data behind it must be real. Every dataset, column, and metric the AI references is confirmed to exist before anything is built, so a made-up column becomes a clear error message, never a broken chart. The error suggests the closest real column names and lists the dataset's columns, so the AI can correct itself in one round.

## Dashboard Design
- **Deterministic Dashboards**: The same spec always produces the identical dashboard. Diff it in git, review it in a PR.
- **15 Chart Types**: big number, big number with trendline, line, bar, area, scatter, categorical bar, pie/donut, table, pivot table, heatmap, histogram, funnel, treemap, and mixed (bars and a line on two value axes, over time or over categories).
- **Metrics As You Write Them**: Saved Superset metrics, `SUM(col)`-style aggregates, `COUNT(*)`, with inline renames (`MAX(pct_of_goal) AS % of Goal`), and custom SQL for ratios the dataset doesn't define: `SQL(100.0 * SUM(on_time) / NULLIF(COUNT(*), 0)) AS On-time %`. `chartwright check` lists custom SQL as unchecked, and apply's data check runs it.
- **Filters and Formatting**: Per-chart WHERE conditions (a column test, or custom SQL such as `{"sql": "amount > 0 OR refunded"}`), a native filter bar, and formatting for table and pivot cells.
    - Filter bar: value pickers, a time range with an optional starting range, numeric sliders, and time grain and time column pickers.
    - Scope any filter to specific charts, the time range included.
    - Cascading filters: a city picker lists only the cities of the region picked (`"dependencies": ["Region"]`).
    - Value pickers can pre-filter their list, sort it by a saved metric, search every value in the database, or exclude what is picked; every filter takes a description, shown as its tooltip.
    - Solid colour rules on pivot and table cells: green, amber or red (Superset's own picker colours), or any hex colour such as `#0057B8`.
    - On Superset 6.1+, a table rule can read one column and paint another, or the whole row: a number coloured by the status beside it.
    - A fixed ascending table sort, d3 number and date formats, and on Superset 6.0+ hidden table columns.
- **Chart Options**: Set the common options of Superset's chart panels in the spec; an option changed in the UI shows up in `plan`.
    - Axes: titles, a fixed floor or ceiling, a log scale; a mixed chart's second axis takes its own.
    - Values written on bars and points, stacked series, 100% stacks, and the top N series of a breakdown.
    - Legends hidden, or placed at the bottom, left or right.
    - Bars in category order (hours, ranks, `1-Mon` weekdays) instead of by value.
    - A time range per chart, such as a "Last 30 days" KPI on a dashboard that shows all time.
    - A trendline KPI's change against an earlier period ("+4% vs last month"), its line colour, and on Superset 6.0+ a subtitle.
    - Tables: page size, a totals row, a search box, column alignment and widths, and on Superset 6.0+ header names.
    - Pivots: averages and other aggregations, rows sorted by value, row subtotals, rows and columns swapped.
    - Heatmap values and colour scheme; what pie, funnel and treemap labels show, and their number format.
- **Goal Lines**: Draw a target or trend line over line, bar, area, scatter and mixed charts with `annotations`: `{"name": "Goal", "value": 80, "style": "dashed"}`, or a formula in x for a trend.
- **Dashboard and Chart Settings**: Set the dashboard's own settings and each chart's in the spec, so a change made in the UI shows up in `plan`.
    - Dashboard: colour scheme, description, certification badge, draft state, auto-refresh interval, and the filter bar across the top (on 4.1.4 and 5.0.0, with Superset's `HORIZONTAL_FILTER_BAR` flag on).
    - Chart: colour scheme, description (viewers open it from the chart menu), certification badge, cache timeout, and a shorter title shown on the dashboard.
    - Tags on Superset 6.0+ (with Superset's `TAGGING_SYSTEM` flag on), and chart timestamps on every card on Superset 6.1+.
- **Named Owners**: List the dashboard's owners in the spec (`"owners": ["jdoe", "ana@example.com"]`), so a dashboard applied from CI belongs to the people responsible for it, and its drafts stay visible to them.
    - Each owner is checked against the instance before anything is written; a misspelt one comes back with the closest accounts.
    - Name owners by email on 4.1.4 and 5.0.0, whose API returns usernames only with `FAB_ADD_SECURITY_API` on; usernames work on 6.1.0.
    - The account that applies stays an owner alongside them: Superset adds it on every import, and a non-admin account needs it to import the next version.
    - `plan` reports owners changed in the UI, and `decompile` reads them back.
- **Dashboard CSS**: `"css"` on the dashboard block holds what you would type into Superset's Edit CSS, so the styling is reviewed and versioned with the rest of the dashboard. CSS changed in the UI is drift that `plan` reports and `apply` replaces.
- **Cross-Filtering, Spec-Owned**: `"cross_filters": true` on the dashboard block turns on Superset's click-to-filter (a value clicked in one chart filters every chart whose dataset has that column, across tabs). Off by default; a toggle made in the UI is drift that `plan` reports and `apply` repairs.
- **No Empty First Load**: New charts open on your full data range, so a narrow default time window never hides everything on the first paint. On a large dataset that full range is a lot to draw, so give the filter bar a time range with a default; `chartwright advise` tells you when a dashboard has nothing bounding its dates.

## Layout Design
- **ASCII Layout Design**: Draw the layout straight from the terminal: `"KKKK LLLLLLLL"` is a KPI card beside a wide line chart.
    - Sizes are ratios drawn as text: more letters make a chart wider, more lines of text make it taller.
    - Stack letters vertically for columns; `.` marks deliberate empty space.
    - An unclear sketch gets a message saying exactly what to fix, never a guess.
    - Every rule drawn and compiled: [the layout guide](LAYOUT-GUIDE.md).
- **Precise Sizing**: Set exact widths and heights per chart (markdown blocks down to one 8 px grid row: `"height": 1.6` is 64 px), or drag a chart taller in the UI and `chartwright absorb` writes the new height back into the spec; widths are a one-line edit in the layout.
- **Rows, Tabs, and Notes**: Even or custom row splits, titled tabs (with one level of sub-tabs, e.g. a sub-tab per row of a scorecard), section headers and dividers between rows (`{"header": "Revenue", "size": "large"}`, `{"divider": true}`), a white card behind a row, and markdown blocks for notes.
- **Header and Footer**: `layout.header` rows sit above everything and `layout.footer` rows below it, outside any tab, so a tabbed dashboard shows them above and under every tab: a banner, a data note, a branding strip, a contact line.
    - Adding a header to a dashboard already in use moves nothing else in it.

## The Design Brain
- **Codified BI/UX Practice**: An optional layer holding what Few, Tufte and IBCS teach about reading a dashboard, plus the Superset rendering quirks that break it, on by default and off with one flag ([full reference](DESIGN-BRAIN.md)).
- **The Brief**: `chartwright brief` prints design guidance for the AI (or you) to read before writing a spec: budgets, chart choice, and composition, tuned to an audience preset (`executive`, `analytical`, or `operational`).
- **The Critic**: `chartwright advise` reviews a finished spec: readable minimum sizes, layout composition (KPIs first, fold budgets, row density), chart-choice limits, narrative polish. `--fix` applies the safe geometry subset; `--profile` adds data-aware checks (a time axis on a non-temporal column, a pie hiding 40 slices); `--chart` looks at one chart.
- **Design Defaults in the Spec**: Leave the small display choices unset and `advise --fix` writes sensible values into the spec, where the diff shows them; `chartwright explain` says where each value came from and how to change it.
    - Monthly time axes labelled `Sep 2026`, counts shown as `12,345`, "vs previous month" after a trendline KPI's change.
    - Tables that page by what fits their panel, a search box on long raw tables, no cell bars behind id, code, year or zip columns.
    - No legend on a single series the title already names, values written on a few bars.
    - A field you write is never touched. A filled value is kept up to date as the chart changes until you edit or delete it; then it is yours, and a deleted one stays deleted. To keep Superset's default from the start, add the rule to `design.ignore`.
    - The bundle depends on the spec alone: compile, `plan` and decompile never add a value of their own.
- **Deliberate Exceptions, Visible**: Suppress any rule per dashboard or per chart in the spec's `design` block; suppressions are reported, never silent.
- **House Style**: A `design.yaml` overlay on your machine tunes thresholds, disables rules, and appends your guidance to the brief, without forking the rulebook.
    - Strict gates (`advise --strict`, `--design strict`) set it aside, so a gate passes or fails the same on every machine.
    - Every review names the overlay: each finding it changed, or, in a strict gate, what was set aside.
- **Heights Calibrated From Your Own Dashboards**: Heights you polish in the UI flow back via `absorb`; `chartwright calibrate` mines them and updates the recommended heights the brief and autofixes use.
- **Design Audits of Legacy Dashboards**: `decompile` + `advise` grades any UI-built dashboard against the rulebook.
- **One-Shot Redesign**: `chartwright redesign <dashboard>` decompiles a live dashboard, audits it, applies the safe geometry fixes, and writes the redesigned spec. A tool-built dashboard is redesigned in place; anything else comes back under a new slug and applies side by side, leaving the original untouched.

## Dashboards as Code
- **Drift Detection**: `chartwright plan` diffs the spec against the live dashboard: charts, filters, scopes, title, CSS, dashboard settings, layout.
- **Start From Existing Dashboards**: Turn any dashboard built in the UI into a spec with `chartwright decompile`, then build it as a copy at a new slug. Most of what it can't carry over is listed; a few settings (such as tab-scoped filters and some legend and tooltip options) are dropped without a note, so compare before you retire the original.
- **Lossless Round-Trips**: Tool-built dashboards with `rows` or `tabs` layouts decompile back with nothing lost; a `sketch` comes back as rows.
- **Targeted Edits**: Replace, rename, resize, or remove one chart and re-apply; old charts are cleaned up, never orphaned.
- **Stable Identity**: Chart ids never change across re-applies, so links, scopes, and open browser tabs stay valid.

## CI/CD and Promotion
- **Environment Promotion**: Specs name their data (connection, schema, table), never instance ids, so the same file applies to dev, staging, and production when they share connection names; where names differ, generate one copy per instance.
- **PR-Gated Dashboard Changes**: Specs live in git; `chartwright plan` passes when the live dashboard matches the spec and fails when it drifted, ready as a merge gate; read-only `chartwright check` runs safely on any schedule.
- **Offline Compilation**: Build the import bundle with `chartwright compile`, no server needed; the output is reproducible byte-for-byte.
- **Instance Migration**: Decompile from one Superset, apply the spec to another; it applies to 4.1.4 and 5.0.0 even when it came from 6.1.0, whose exports those releases reject. `chartwright check` names any setting the target release can't take, such as tags before 6.0, for you to remove first.
- **Git as the Source of Truth**: A lost or mangled dashboard is one re-apply away from its spec.

## Safety and Recovery
- **Automatic Backups**: Every apply to an existing dashboard saves the live state first, no flag needed; backups are named to the microsecond and never overwritten.
- **Complete Restore**: `chartwright restore` brings back the dashboard, chart settings, and filter scopes.
- **Self-Healing Applies**: When an apply fails while preparing, importing or updating charts, the previous state is restored automatically, whatever the error. A failure after that (linkage, filter scopes, chart queries) leaves the new version live, and the report gives the backup to restore.
- **Stale-Tab Protection**: An old browser tab writing back stale state is detected by `plan` and repaired by `apply`.
- **Verified at Every Step**: References are checked before anything is written, the finished dashboard is compared chart-by-chart against the spec, and every chart's query is run once: an error fails the apply, and a chart that returns no rows is named. Any failure says what went wrong and where.
    - Your spec is held to the instance's Superset release too: a setting the release can't take (tags on 4.1.4 or 5.0.0, chart timestamps before 6.1) stops the apply before anything is written and names the field to remove; one it would ignore, such as a trendline subtitle before 6.0, comes back as a warning. Offline, `chartwright compile --superset-version 5.0.0` runs the same check.
- **Ownership Guard**: The tool only ever overwrites dashboards it created. To manage a hand-built dashboard, decompile it into a spec and build it at a new slug; the original stays untouched.

## Enterprise Ready
- **Multiple Instances**: Sandbox, staging, and production as profiles in one file.
- **Secrets Stay Out of Your Profiles**: Point each profile at where its secret already lives.
    - Self-hosted Superset signs in with a username and password (database or LDAP provider): take the password from an env var (any name) or your credential manager (1Password, macOS Keychain, sops). Chartwright never signs in through SSO or OAuth; on an instance where people use SSO, ask your admin for an account that has a Superset password.
    - Preset-hosted workspaces (preset.io) sign in with an API token and secret: take them from env vars, or reuse the credentials preset-cli already stored.
- **What the AI Can See**: The AI proposes; the tool verifies, using your own Superset login. Verification reads names (datasets, columns, metrics), not rows; the AI never queries your warehouse.
    - The post-apply data check keeps a row count and discards the rows.
    - Custom SQL in a spec runs with the profile's rights during apply's data check; `check` lists it under `unchecked_sql` so you can review it before it runs, and the apply report lists it too.
- **Corporate Networks**: Custom CA bundles, proxies, LDAP auth, internal pip mirrors (only 3 dependencies).
- **Works Where You Work**: Windows, macOS, Linux; PowerShell and git bash; run from any directory; the Claude Code skill installs by copy, no admin rights.

## More Ways to Use It
- **Dashboard Documentation**: Decompile any dashboard into a readable inventory of its charts, metrics, and filters.
- **Cloning**: Copy a spec, change the slug and the data it points at, apply. A proven dashboard becomes a starting point; a short script makes one copy per team or tenant (specs hold literal values, no template variables).
- **Programmatic Dashboards**: Specs are JSON; generate or edit them with scripts, one dashboard per file.
- **Drift Audits**: Run `plan` on a schedule to catch UI edits that diverged from the reviewed spec.
- **Training and Demo Environments**: Apply the same spec to each training or demo instance, and every student or demo gets the identical dashboard.
- **Safe Experiments**: Try a redesign under a new name, compare side by side with the original, delete nothing.

## CLI Verbs
| Verb | What it does |
|---|---|
| `chartwright schema` | Print the spec's JSON Schema, the contract for people and AIs |
| `chartwright validate` | Check a spec against the schema, offline |
| `chartwright brief` | Print the design guidance to read before writing a spec |
| `chartwright advise` | Review a spec against the design rulebook; `--fix` applies the safe repairs and fills design defaults into the spec; `--chart` looks at one chart |
| `chartwright explain` | Show, per chart, where each design-default field's value comes from (the spec, a fill, or Superset) and how to change it; `--json` for agents |
| `chartwright compile` | Build the import bundle, no server needed |
| `chartwright check` | Verify every dataset, column, and metric against a live instance, read-only |
| `chartwright apply` | Build, import, and verify the dashboard end to end |
| `chartwright plan` | Show what differs between the spec and the live dashboard |
| `chartwright decompile` | Turn a live dashboard into a spec |
| `chartwright redesign` | Decompile a live dashboard, audit it, and write the repaired spec |
| `chartwright absorb` | Pull height polish made in the UI back into the spec |
| `chartwright calibrate` | Propose recommended heights from your absorb history |
| `chartwright restore` | Bring back a backed-up dashboard, completely |

## Testing and Evidence
- **The full offline suite** on Linux and Windows on every pull request and every push to main ([exact counts](VERIFICATION.md)).
- **Live CI against real Superset 4.1.4, 5.0.0, and 6.1.0** on every pull request and every push to main: full apply, lifecycle soak, stale-tab adversary, fault injection.
- **500-cycle soak** passed on the oldest and newest supported versions.
- **Chart options verified against Superset's own source code** for every supported version, so a Superset change surfaces here before it reaches your dashboards.
- Full evidence: `docs/VERIFICATION.md`; source citations: `docs/CONTRACTS.md`.

## Deliberately Not Included
- Exotic chart types (maps, sankey, gauge): added when a real dashboard needs one (ask); until then those dashboards live on in the UI, untouched.
- Per-chart cross-filter scoping (which charts emit or receive): the on/off switch is in the spec; finer scoping waits for real demand.
