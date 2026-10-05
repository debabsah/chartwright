# Verification

How this tool is tested, what each layer proves, and how to reproduce all of
it. The companion page, `docs/CONTRACTS.md`, cites every Superset behavior
the tool depends on to Superset's source at releases 4.1.4, 5.0.0, and
6.1.0; this page covers what is proven by actually running the tool.

## What is tested, and why

The correctness guarantee lives in typed code: the spec is validated, every
reference is resolved before anything is built, and the result is verified
after import. Testing therefore concentrates on the dimensions that vary in
real use and that a single test run holds constant:

- **Version**: Superset 4.1.4, 5.0.0, and 6.1.0, as real containers rather
  than mocks.
- **Environment**: Linux and Windows, which differ in text encoding, path
  rules, and zip format details.
- **Time**: hundreds of edit, apply, and verify cycles per dashboard rather
  than one.
- **Other writers**: a scripted second writer that overwrites dashboard
  metadata the way a stale browser tab does. Superset stores dashboard
  metadata last-writer-wins in every supported version (CONTRACTS, "How
  dashboard settings are stored").
- **Faults**: injected failures at every stage boundary of `apply`.

Every layer below runs in CI on every pull request, every push to main and
every manual run: the offline suite on
`ubuntu-latest` and `windows-latest`, then per-version live jobs that boot a
real `apache/superset` container at each of the three releases and run the
live check, a 25-cycle soak, the second-writer scenarios, and fault
injection.

## The matrix

| Layer | Proves | Where it runs |
|---|---|---|
| Offline suite (1524 tests, 65 modules) | Contract, determinism, round-trips, credentials | every PR and push to main, Linux + Windows, mcp 1 and 2 |
| Chart-option contract | Every emitted chart option is declared by each version's plugin source | every PR and push to main |
| Live guarantee check | Three specs applied (every chart type, every display control, every dashboard and filter control), per-chart data check, ids stable across re-apply | every PR and push to main, all 3 versions |
| Standards content round trip | `standards apply` writes an org footer, a team header and two CSS blocks into a spec; it applies, `plan` stays clean, decompile reads the CSS markers and every managed row back, `--claim` rebuilds the record, re-apply stays clean | every PR and push to main, all 3 versions |
| Locked text visible | `standards verify-visible` in headless Chromium: the standards fixture's locked footer shows, and the same dashboard with near-white footer CSS is caught. Installing the browser may fail without failing the job (the check is then skipped); once installed, a failing check fails it | every PR and push to main, all 3 versions |
| Lifecycle soak | 500 randomized edit cycles with invariants held | 500 cycles on 6.1.0 and 4.1.4 before release; 25 cycles per version on every PR and push to main |
| Second-writer scenarios | Stale-tab overwrites detected by `plan`, repaired by `apply` | every PR and push to main, all 3 versions |
| Fault injection | A typed failure at every stage boundary; complete restore | every PR and push to main, all 3 versions |
| Real-instance use | Production 4.1.x on Windows, real datasets, real users | ongoing |

## 1. Offline suite

The counts in the table above are the only hard numbers in the docs, and
`tests/test_docs.py` fails when they drift from what pytest actually
collects, the same "generated, not hand-maintained" rule the rule table in
[DESIGN-BRAIN.md](DESIGN-BRAIN.md) §7 follows.

- **Spec contract** (`test_spec.py`, `test_spec_v2.py`): validation
  semantics. A layout is rows or tabs, never both; row widths must fit the
  grid; chart names must be unique (duplicate names would silently collide
  as identities); filter bounds and value shapes are checked; unknown
  fields are rejected everywhere, so a typo fails loudly instead of being
  ignored.
- **Deterministic compiler** (`test_compiler.py`): compiled bundles are
  compared byte for byte against checked-in reference copies, with zip
  timestamps and entry order pinned, so the same spec produces the
  identical bundle on any build platform.
- **Lossless round-trips** (`test_decompile.py`): decompiling a compiled
  spec reproduces that spec exactly, across the full surface (15 charts
  covering all 15 chart types, chart filters, the native filter bar,
  markdown, tabs), and stays stable under a second round-trip. Decompiling
  a real 25-chart export that uses unsupported chart types names every
  loss; nothing drops silently.
- **Randomized round-trips** (`test_property_roundtrip.py`): a seeded
  random editor mutates a spec while keeping it valid (the same generator
  the live soak uses) through 20 seeds and 6 steps each; compile then
  decompile must reproduce the spec at every step. Seeds make every
  failure exactly reproducible.
- **Credential portability** (`test_profiles.py`): passwords from an
  environment variable of any name, usernames from an environment variable
  (with the literal value taking precedence), credential-manager commands
  in both shell-string and argument-list form, home-directory expansion,
  TLS options, and a named error when no credential source exists.
- **Drift and plan normalization** (`test_dashdiff.py`), **MCP parity**
  (`test_mcp_server.py`: the MCP tools mirror the CLI one to one),
  **backup layout** (`test_backup_layout.py`: backups are separated per
  profile and the location override is honored), plus dedicated suites for
  pivot formatting, range filters, layout sketches, and absorb.
- **Dashboard and filter controls** (`test_dashboard_settings.py`,
  `test_chart_metadata.py`, `test_annotations.py`, `test_filter_controls.py`,
  `test_sql_metrics.py`, `test_layout_headers.py`): each setting compiles to
  the shape Superset saves, compiles to the earlier output when omitted,
  decompiles back, shows up in `plan` when changed live, and names a bad
  value.
- **Release-specific fields** (`test_superset_version.py`): with the
  instance's answer mocked, `check`, `apply` and `plan` refuse tags before
  6.0.0 and chart timestamps before 6.1.0 ahead of any write, warn for the
  fields older releases ignore, and read the version from `/version` (6.1.0)
  or the sign-in page (4.1.4, 5.0.0); `compile --superset-version` gives
  the same answers and the same bundle.
- **The design brain** (`test_design*.py`, `test_calibrate.py`,
  `test_redesign.py`): every rule table-driven against violating and clean
  specs; fix-loop convergence, idempotence, and the no-fractional-heights
  invariant; a seeded advise-never-raises fuzz; the chart-type taxonomy
  contract; design.yaml trust-boundary validation; the golden dogfood
  (the shipped example raises nothing but pending design defaults, and
  nothing at all once `--fix` writes them); calibration grouping, decay,
  and overlay round-trips.
- **Design defaults** (`test_design_defaults.py`): each `default.*` fill
  fires where its conditions hold and nowhere else, never touches a field
  the author wrote (a written Superset default included), keeps its own
  fills current through the values `design.filled` records, keeps an
  author's edit to a filled value, never refills a deleted one, and never
  writes Superset's own value. Filling every
  fixture converges, a second `--fix` is a no-op, a paged table never
  ratchets `size.table-window` across heights 6-20, fills never write
  geometry or a field a repair writes, and a fixed spec compiles to the
  same bundle as the same fields with no `design` block.
- **Layout header** (`test_layout_header.py`): header rows sit at grid
  level above the tabs, rows or sketch; adding one changes no body or footer
  node, chart placeholders included; it round-trips losslessly and
  recompiles to the same bundle; rows above a UI-built dashboard's tabs read
  back as its header; the critic advises header rows in place, spends the
  header in every tab's fold budget, and fixes header markdown by its own
  address.
- **Standards** (`test_standards.py`): `extends` merges each key as
  documented and records the layer behind it; cycles, unknown parents, a
  fourth file, unknown rule ids and parameters, and every way a lower file
  could loosen a lock are typed errors; discovery stops at the repository
  root and refuses two folders; `design.standard`, the default and
  `standards assign` pick and write the standard; a locked rule survives
  `design.ignore`, `--ignore`, `design.yaml` and a fractional height;
  `standards check` exits on the right findings and reads nothing from
  `design.yaml`; the fleet report's shape; the MCP tools return what the CLI
  prints; without a standards folder the advice is unchanged; and every
  example and fixture compiles to the same bytes with `design.standard` set.
- **Waivers** (`test_standards_waivers.py`): the waivers file is no
  standard and makes no standards folder; every malformed entry is a typed
  error naming it; a waiver lets a locked content item or a locked rule pass
  for its dashboard, by slug or spec path, scoped to a layer when it says so,
  reported with owner, reason and expiry, while `design.ignore` still can't;
  an expired waiver fails `standards check` and `advise` only for the specs
  checked, and `--as-of` reproduces a run; a deploy's advice and `apply` warn
  and pass a strict gate; `restore` never reads the folder; `standards apply`
  leaves a waived item (even before the first apply) and `--check` passes
  it; the report lists expired, expiring and unmatched waivers; the MCP
  tools match the CLI; without a waivers file payloads are as before.
- **A standard's release floor** (`test_standards_versions.py`):
  `min_superset` reads as a release and `standards show` names it; `standards
  check --superset-version` holds back a floor's content and a theme, never
  expects them and keeps their records; `check`, `apply`, `plan` and MCP
  `check_spec` hold the standard's content on an older instance instead of
  refusing it, while an author's own theme is still refused; the release is
  asked only when something could be held, and an unknown one holds nothing
  and says so.
- **Classification locks** (`test_standards_classification.py`): a
  standard's classification is written and recorded with its rows in one
  run; unlocked it is the author's to change; locked, a change or a removal
  is an error, its rows follow the standard's value and `--locked` restores
  it without losing them; a waiver lets one dashboard differ; the value must
  be in the list and a lower layer can't change it.
- **Dashboard theme** (`test_dashboard_theme.py`): the name is validated;
  4.1.4 and 5.0.0 refuse it before any theme lookup; resolve finds the exact
  name and names an unknown, ambiguous or unreadable theme; the bundle
  carries the resolved `theme_id` and nothing without a theme; decompile
  reads the name from an export and names what it can't; `plan` compares it
  when the spec names one and leaves a UI-chosen theme alone otherwise.
- **Visible locked text** (`test_visible.py`): the verdict over a fake
  page's measurements names each way text is hidden (missing, unrendered,
  hidden, sizeless, off the page, cut off, transparent, clipped, tiny, blurred,
  low contrast, covered); the measurements real Chromium recorded on 4.1.4,
  5.0.0 and 6.1.0 for ten hiding stylesheets and the unchanged dashboard
  (`fixtures/visible/measurements.json`) get the right verdict; the browser
  script returns exactly the facts the verdict reads; the targets are the
  locked rows the spec holds; the command exits 1 naming hidden items, reports
  a timeout, an untrusted certificate or a browser failure as a typed error,
  keeps certificate checks on for a `ca_bundle` profile, and without the
  visual extra says how to install it; Playwright is no core dependency. A
  live test runs the real browser when `CHARTWRIGHT_VISIBLE_LIVE` names a
  Superset.
- **Dashboard owners** (`test_dashboard_owners.py`): owners never reach
  the bundle; usernames resolve where the security API answers and emails
  everywhere, an unknown or ambiguous owner is a resolve-stage error with
  candidates and stops `apply` before any write, the signed-in account is
  read from the token and always kept, `apply` PUTs the ids after the
  import and fails at its own stage when refused, decompile names the live
  owners (or leaves them out with a loss when one can't be named), and
  `plan` compares ids, ignoring owners when the spec omits them.
- **Strict gates and the per-machine overlay** (`test_overlay_gates.py`): a
  `design.yaml` that disables a rule, lowers a severity or moves a threshold
  leaves `advise --strict`, `--design strict` and the MCP strict modes
  failing exactly as with no overlay, a raised severity is set aside too,
  and the payload lists everything set aside; without a strict gate the
  overlay applies and the payload names each finding it changed.

## 2. Chart options, checked against plugin source

Superset's backend has no schema for chart options; each chart type's
options are defined only by its frontend plugin. The file
`tools/contracts/params-contract.json` holds the option names for the 13
emitted chart types, extracted from plugin source at each supported
release (the Mixed Chart's by `tools/extract_mixed_contract.py`), and `tools/params_drift.py` (run by `test_params_contract.py`)
fails the build if the compiler ever emits an option a target release does
not declare. Current status: clean against all three releases; the single
tool-owned key (`sdc_categorical_bar`) is allowlisted with its rationale
recorded. Option drift in Superset's plugins, the natural failure mode of
any tool that writes chart options, becomes a failing build here instead of
a runtime surprise.

## 3. Live guarantee check (`tools/ci_live_check.py`)

Runs against a real instance, start to finish. First, pre-flight
resolution, which collects every bad reference into typed errors, and a
deliberately misspelled column, which must come back with the real column
as its first suggestion. Then an
apply of the complete example spec (`tests/fixtures/kitchen_sink.json`),
which exercises all 15 chart types, per-chart WHERE filters, a native
filter bar with two value pickers and a time range, plus markdown and tabs.
(A numeric range filter scoped to specific charts is exercised live by the
second-writer and fault-injection runs.) The same check then runs on two
more specs, `tests/fixtures/live_display_controls.json` (every chart display
control: legends, axis titles and bounds, stacking, labels, table and pivot
options) and `tests/fixtures/live_dashboard_controls.json` (dashboard
settings and owners, colour schemes, goal lines, a header and footer
outside the tabs, header rows inside them, cascading and
pre-filtered native filters, time grain and time column filters). They are
the offline display and dashboard controls fixtures pointed at Superset's
example data. Each apply is followed by a per-chart data check: each
chart's query, over the chart's own time range, must return HTTP 200 and
rows; an empty chart is a named
warning, never a silent pass. Finally a second apply of the same spec,
asserting that every chart keeps its id. Id stability matters because
dashboard metadata references charts by id: changing ids is what turns a
stale browser tab into a writer that corrupts filter scopes.

## 4. Lifecycle soak (`tools/soak.py`)

The loop users actually live in, run to exhaustion. Each cycle applies one
seeded random edit within the supported surface (add, remove, or rename
charts; retitle; add, remove, or rescope filters; regenerate the layout;
switch between rows and tabs) and then asserts, against the live instance:

1. `apply` completes, including link verification and the per-chart data
   check;
2. surviving charts keep their ids;
3. `plan` is clean immediately after `apply`;
4. decompiling the tool's own dashboard loses nothing;
5. periodically, a dry-run `absorb` changes nothing (heights round-trip).

Before release, this ran 500 consecutive green cycles on 6.1.0 and on 4.1.4
(the 4.1.4 run grew to 20 charts and 10 filters under the random edits).
Every failure dumps the seed, the spec, and the report for one-command
reproduction.

## 5. Second-writer scenarios (`tools/adversary.py`)

Superset stores dashboard metadata last-writer-wins in every supported
version, and on 4.1.x a closing browser tab writes its whole stale copy
back (CONTRACTS has the file and line citations). This harness performs
those overwrites deliberately and asserts that `plan` detects each one and
`apply` repairs it without changing any chart id:

- A tab from before a filter was rescoped writes the old scope back.
- A tab from before a filter existed erases that filter entirely.
- Filter scopes are corrupted to reference deleted chart ids, which the
  server accepts silently; `plan` recomputes the scopes and refuses to
  call the dashboard clean.
- A dashboard with cross-filtering turned off keeps that setting through
  the tool's own writes (the API turns it back on when the key is
  omitted).

All four scenarios pass on 4.1.4, 5.0.0, and 6.1.0.

## 6. Fault injection (`tools/faultline.py`)

A wrapping client trips a chosen method with the same typed error a real
network drop produces, a fake HTTP 500 for a rejected request, or a plain
`RuntimeError` standing in for a bug in the tool itself. A fault at every
stage boundary (the dataset round-trip and stale-chart deletion during
preparation; an importer 500, a mid-import drop and an import-time bug;
a rejected or dropped in-place chart update; the post-import link
verification; the data check) must produce a typed report naming the
stage, never a traceback, and a recorded backup whenever mutation had
begun. Then:

- for faults during preparation or import, in-place chart updates
  included, the automatic restore fires whatever the error type, and
  `plan` against the pre-apply spec comes back clean: the dashboard is
  back, not half-updated;
- for faults after import, a clean re-apply converges.

Restore is verified to be complete. Because Superset's importer never
overwrites existing charts, a naive re-import restores only the dashboard
shell. The restore path additionally writes surviving charts' settings
back from the backup and recomputes filter scopes from the backup's own
name-based markers; the test changes chart settings and scopes, restores,
and requires `plan` against the old spec to be spotless.

## 7. Real-instance use

Beyond sandboxes, the tool is used against a production Superset 4.1.x
instance on Windows by a working analyst: real warehouse datasets,
dashboards used weekly, real concurrent browser sessions. That round of use
produced an incident ledger (every entry root-caused and closed; the
defects appear in the table below) and directly motivated the soak, the
second-writer scenarios, and fault injection.

## Defect ledger: what each layer caught

Twenty-two real defects found by these layers, none of which the original
unit suite could see. The layer that caught each one is the reason that
layer exists.

| # | Defect | Caught by |
|---|---|---|
| 1 | On Windows, spec files were read in the legacy cp1252 encoding, corrupting UTF-8 text | real-instance use |
| 2 | The 4.x dashboard detail API omits the uuid, so the ownership check refused the tool's own dashboard | real-instance use |
| 3 | Zip metadata varies by platform, so compiled bundles differed between Windows and Linux | real-instance use |
| 4 | A metadata round-trip left unscoped filters applying to no charts at all | real-instance use |
| 5 | Re-apply deleted and re-imported charts, changing their ids; stale browser tabs then corrupted filter scopes | real-instance use; fixed at the root by updating charts in place |
| 6 | `apply` passed a list where the chart lookup expects a dict (introduced during a merge) | live check, first run |
| 7 | A Windows executable path, embedded in a quoted TOML test fixture, broke on backslashes | Windows CI, first run |
| 8 | A range filter scoped to specific charts could not be rebuilt from a backup; the scope dropped silently | randomized round-trips, seeds 6 and 9 |
| 9 | `plan` could not run against the tool's own dashboard on 4.x; it depended on the uuid the detail API omits there | soak on 4.1.4, cycle 1 |
| 10 | On 4.x and 5.0, renaming or removing a chart left the old chart still linked to the dashboard, because import merges links instead of replacing them | soak on 4.1.4, cycle 10 |
| 11 | Renaming a chart left the old chart behind as an orphan, on every version | fixed by the same root fix as #10: the tool deletes its own charts once they leave the spec |
| 12 | `plan` never compared filters, so the most common real-world drift was invisible to it | designing the second-writer scenarios |
| 13 | Filter scopes pointing at deleted chart ids read back as clean | second-writer scenario: corrupted scopes |
| 14 | Restore brought back the dashboard but not the surviving charts' settings | fault injection: restore design |
| 15 | Restore left filter scopes at their defaults instead of the saved ones | fault injection: restore completeness |
| 16 | Big-number subtitles rendered at the full card height on 6.1.0, huge and cropped, because the compiler left font sizes to a plugin fallback | building the public demo on real data |
| 17 | The params drift checker compiled a fixture with no subtitles, so the conditional `subheader` key was never checked and its 6.1.0 rename went unnoticed | auditing every visual control after #16 |
| 18 | Charts without their own time column ignored the dashboard time filter entirely, while the filter bar still counted them as filtered | live check of the time filter default: a one-week filter left a full-month total |
| 19 | `plan` crashed on any sketch-layout spec; every prior fixture and soak used rows or tabs | running `plan` against the demo dashboard |
| 20 | A labeled `COUNT(*)` metric lost its label on decompile, so `plan` reported drift forever on clean dashboards | the same `plan` run, after #19 was fixed |
| 21 | The client treated a rate-limited response (HTTP 429) as fatal instead of backing off, and PUT requests skipped the typed-error wrapper entirely | live CI: Superset rate-limited a burst of decompile lookups |
| 22 | Charts drawn on a time axis (line, bar, area, scatter, trendline KPI, mixed over time) ignored both their own `time_range` and the dashboard time filter, on every release: they had no time-range filter on their axis | live calibration: a line chart limited to March 2004 to March 2005 still drew 2003 to 2005 |

## Reproduce everything

```bash
# offline (any machine, no Superset needed)
python -m pytest tests/ -q                 # the offline suite
python tools/params_drift.py --all         # chart options vs plugin source, 3 versions

# live (any sandbox; sandbox/up.sh --tag <v> boots one)
export SDC_CI_PASSWORD=admin
python tools/ci_live_check.py --base-url http://localhost:8098   # kitchen sink
python tools/ci_live_check.py --base-url http://localhost:8098 --spec tests/fixtures/live_display_controls.json
python tools/ci_live_check.py --base-url http://localhost:8098 --spec tests/fixtures/live_dashboard_controls.json
python tools/ci_live_standards.py --base-url http://localhost:8098   # tests/fixtures/standards_live
python tools/ci_live_ui_chart.py --base-url http://localhost:8098    # a chart added in Superset
python tools/ci_live_adopt.py --base-url http://localhost:8098       # adopt in place
pip install -e ".[visual]" && playwright install chromium
python tools/ci_live_visible.py --base-url http://localhost:8098     # locked text visible
python tools/record_visible_measurements.py --base-url http://localhost:8098   # refresh the fixture
python tools/soak.py       --base-url http://localhost:8098 --cycles 500 --seed 1
python tools/adversary.py  --base-url http://localhost:8098
python tools/faultline.py  --base-url http://localhost:8098
```

CI definition: `.github/workflows/ci.yml`.

## Known coverage limits

Stated plainly, so the green above means something:

- **Auth**: Chartwright signs in through Superset's password login API
  (database or LDAP accounts) or with Preset API tokens; it can't complete an
  SSO or OAuth sign-in. CI signs in with a database login; LDAP and Preset
  sign-in aren't tested live.
- **Versions**: 4.1.4, 5.0.0, and 6.1.0 exactly; other release lines are
  untested. 6.0.x is not in the tested matrix: the release-specific fields
  are placed at 6.0.0 or 6.1.0 from the 6.0.0 source, never from a running
  6.0.x. Reading the instance's version is tested against responses shaped
  like each release's source, not yet against the live containers.
- **Concurrency**: the second-writer harness scripts the known stale-tab
  patterns; arbitrary multi-writer races are not exhaustively explored.
- **Permissions**: all verification runs as an admin. Restricted roles
  (permission errors, datasets hidden by row-level security) are untested.
- **Rendering**: layout correctness is verified structurally and by spot
  screenshots, not by pixel comparison; visual taste remains human.
- **Data engines**: live layers run against Superset's example datasets
  plus one production warehouse. Other engines (Presto, BigQuery, and the
  rest) are untested; the tool only reads metadata and issues chart-data
  queries, but engine-specific column-type quirks could surface in the
  data check.
